"""Training data for the fine-tuned Text-to-SQL model (Step 6b-a).

Gemini writes question -> SQL pairs in batches. Every SQL goes through the same validator and read-only
executor as the real tool, and a pair is kept only if it returns something meaningful. Kept pairs are
appended to finetune/data/raw.jsonl after each batch, so a run can stop at any time (the free tier allows
about 20 requests per model per day) and the next run carries on. Each batch asks for the category with
the fewest kept pairs, so categories that get rejected more often simply get more requests.

The train/test split hashes the *shape* of the SQL (literals, aliases and table qualifiers removed), not
the pair. Paraphrases and "same query, other machine" pairs therefore stay on one side: a random split
would put them on both and overstate accuracy. Hashing also means new data never moves an old pair
between train and test.

    python -m ida.training_data generate --max-requests 30     # add pairs, resumes from raw.jsonl
    python -m ida.training_data split                          # raw.jsonl -> train.jsonl + test.jsonl
    python -m ida.training_data recheck                        # apply new rules to the pairs already kept
    python -m ida.training_data stats                          # counts per category, model and split
"""
import argparse
import hashlib
import json
import logging
import random
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import exp

from ida.config import DATA_END, ROOT, get_settings
from ida.mcp_server.db import QueryTimeoutError, run_query
from ida.mcp_server.sql_generator import build_prompt, load_schema
from ida.mcp_server.sql_validator import SQLValidationError, validate_sql
from ida.model_pool import AllModelsUnavailable, ModelPool, default_memory

log = logging.getLogger(__name__)

DATA_DIR = ROOT / "finetune" / "data"
RAW_PATH = DATA_DIR / "raw.jsonl"
REJECTED_PATH = DATA_DIR / "rejected.jsonl"

TARGET_PAIRS = 1500
BATCH_SIZE = 20
TEST_PERCENT = 10
TEMPERATURE = 0.9            # variety matters more here than in the agent
PAUSE_S = 6                  # stays under the per-minute limit
BENCHMARK_MODEL = "gemini-3.1-flash-lite"    # never writes pairs: it is the Gemini scored on the test split
ASKS_FOR_QUALITY = re.compile(r"good|quality|uncertain|reliable|valid|trust", re.IGNORECASE)
FINGERPRINT_ROWS = 5000      # enough to tell two queries apart
TIE_TOLERANCE = 1e-6         # julianday() arithmetic: two 8 h events can differ in the 9th decimal
MEASURED = {"value", "peak_value"}                     # unit depends on the sensor type
ONE_UNIT = {"sensor_type", "unit", "sensor_id", "mount_location"}    # columns that narrow those to one unit

CATEGORIES = {
    "filter_count": "Single-table lookups, filters and counts (machines by status/plant/line, sensors by "
                    "type, install dates, thresholds).",
    "joins": "Joins over 2-4 tables: machines-sensors, sensors-readings, alarms-machines, alarms-sensors, "
             "maintenance_events-machines. Return machine names, not ids.",
    "aggregates": "GROUP BY with COUNT/SUM/AVG/MIN/MAX, HAVING, and aggregates per machine type, plant, "
                  "line, sensor type or manufacturer.",
    "time_windows": "Time filters and buckets: a given day, week or month, 'last week', 'the last 3 days', "
                    "daily or hourly aggregates, night vs day shift, using strftime(), date() and datetime().",
    "reading_quality": "The NULL trap: bad readings have value NULL and quality 'bad'. Share of bad or "
                       "uncertain readings, data gaps per sensor, averages over good readings only, sensors "
                       "with the most missing data.",
    "active_alarms": "The cleared_at trap: an alarm is active when cleared_at IS NULL. Active alarms, "
                     "unacknowledged alarms (acknowledged_by IS NULL), COMM_FAULT vs HIGH vs HIGH_HIGH, "
                     "severity, who acknowledged what.",
    "durations": "Durations with julianday(): alarm duration in minutes (cleared alarms only), time to "
                 "acknowledge, maintenance downtime in hours, MTTR per machine.",
    "thresholds": "Readings compared with sensors.warn_threshold and crit_threshold, peak_value in alarms "
                  "against thresholds, margin to the limit, time spent above a limit.",
    "maintenance": "maintenance_events: cost_eur totals by type, technician or machine, corrective events "
                   "linked to alarms through related_alarm_id, machines never serviced, last service date.",
    "ranking_cte": "Top-N, worst and best per group, using CTEs and window functions (RANK, DENSE_RANK, "
                   "LAG), comparing one group with another, and machines with no alarms (LEFT JOIN).",
}

# What the SQL of a category must contain. The model drifts off-topic, and the balancing counts these
# labels, so a mislabelled pair would hide that a trap category is really short of examples.
CATEGORY_SQL = {
    "time_windows": r"strftime|date\(|datetime\(|\bts\b|_at\b|_time\b|install_date",
    "reading_quality": r"\bquality\b|value IS (?:NOT )?NULL",
    "active_alarms": r"\bcleared_at\b|\backnowledged_",
    "durations": r"julianday",
    "thresholds": r"_threshold",
    "maintenance": r"maintenance_events",
}
OFF_CATEGORY = "general"     # valid pair, not counted towards any category

STYLES = [
    "plain full sentences, as a plant manager would ask",
    "short analyst phrasing, e.g. 'avg vibration by machine type, September'",
    "direct data requests, e.g. 'Get the daily maximum temperature of Compressor-03.'",
    "a mix of formal and casual wording, including some indirect questions",
]

FOCI = ["CNC Mill machines", "Compressor machines", "Pump machines", "Conveyor machines",
        "Hydraulic Press machines", "Robot Arm machines", "Plant A vs Plant B", "single named machines",
        "specific dates in August or September 2026", "the whole plant"]


def db_values(db_path: Path) -> str:
    """Real column values, so questions use names that exist instead of coming back empty."""
    def distinct(table: str, col: str) -> str:
        rows = run_query(db_path, f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY 1",
                         1000, 10).rows
        return ", ".join(str(r[0]) for r in rows)

    lines = [f"{t}.{c}: {distinct(t, c)}" for t, c in [
        ("machines", "name"), ("machines", "machine_type"), ("machines", "manufacturer"), ("machines", "plant"),
        ("machines", "line"), ("machines", "status"), ("sensors", "sensor_type"), ("sensors", "mount_location"),
        ("alarms", "alarm_type"), ("alarms", "severity"), ("alarms", "acknowledged_by"),
        ("maintenance_events", "event_type"), ("maintenance_events", "technician")]]
    return "\n".join(lines + ["Readings run every 15 minutes from 2026-08-02 00:00:00 to 2026-09-30 23:45:00."])


def batch_prompt(schema: str, values: str, category: str, style: str, focus: str, covered: list[str],
                 n: int = BATCH_SIZE) -> str:
    avoid = "\n".join(f"- {q}" for q in covered) or "- (nothing yet)"
    return f"""Write {n} different pairs of a question and the SQLite query that answers it, for training a
Text-to-SQL model on the database below.

### Schema
{schema}

### Real values
{values}

### This batch
Category (EVERY pair must be of this kind): {CATEGORIES[category]}
Focus (for about a third of the pairs; the rest should use other machines, plants and sensors, but stay
in the category): {focus}
Question style: {style}

### Rules for the SQL
- It must answer exactly what the question asks, nothing more. Write it on one line.
- Use only tables and columns from the schema, and SQLite functions only.
- The data ends at {DATA_END}: that is "now". 'Yesterday' is 2026-09-30 and 'last week' is the 7 days
  before {DATA_END}. Never use date('now') or CURRENT_TIMESTAMP.
- readings.value is NULL when quality = 'bad', and AVG/MIN/MAX skip NULL by themselves. Filter on quality
  ONLY when the question asks for it: 'uncertain' readings count unless the question says otherwise.
- An alarm is active when cleared_at IS NULL.
- Rankings must show ties: for "the highest", "the most" or "top N" use RANK() or = (SELECT MAX(...)), so
  that every tied row appears. ORDER BY ... LIMIT and ROW_NUMBER() are rejected if they cut through a tie.
  LIMIT always needs an ORDER BY.
- readings.value and alarms.peak_value have a different unit per sensor type (degC, bar, rpm, ...). Only
  aggregate or rank them within one sensor type or sensor. Ratios to a threshold are fine.
- Answer "which machine" questions with machine names, not ids. No SELECT *, no comments, no double quotes.
- Pick filters that match real rows: the result must not be empty, NULL or a count of 0.
- Range from easy to hard. Vary the columns, filters and SQL structure; do not just change a name.

### Already covered, do not repeat these
{avoid}

Return only a JSON array: [{{"question": "...", "sql": "..."}}, ...]"""


def parse_batch(text: str) -> list[dict]:
    """The pairs in a model reply; [] if it is not a JSON array."""
    start, end = text.find("["), text.rfind("]")
    try:
        items = json.loads(text[start:end + 1]) if start != -1 else []
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)
            and isinstance(i.get("question"), str) and isinstance(i.get("sql"), str)]


def question_key(question: str) -> str:
    return re.sub(r"\W+", " ", question.lower()).strip()


def same_value(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < TIE_TOLERANCE
    return a == b


def first_sort_key(select: exp.Select) -> exp.Expression:
    """The first ORDER BY key as an expression that can be selected: aliases and `ORDER BY 2` resolved.
    Later keys are ignored because a tiebreaker such as `name` only hides the tie."""
    key = select.args["order"].expressions[0].this
    if isinstance(key, exp.Literal) and key.is_int:
        return select.selects[int(key.this) - 1].unalias()
    if isinstance(key, exp.Column) and not key.table:
        for column in select.selects:
            if column.alias == key.name:
                return column.unalias()
    return key


def limit_cuts_tie(db_path: Path, tree: exp.Expression, select: exp.Select, timeout_s: float) -> bool:
    """Reruns one LIMIT with a row more, plus its sort key, to compare the rows on each side of the cut."""
    limit, offset = select.args["limit"].expression, select.args.get("offset")
    if not isinstance(limit, exp.Literal):
        return False
    start = int(offset.expression.this) if offset else 0
    end = start + int(limit.this)
    probe = select.copy()
    probe.select(first_sort_key(select).copy(), copy=False)
    probe.limit(end + 1, copy=False)
    probe.set("offset", None)
    if select is not tree and tree.args.get("with_"):       # a LIMIT inside a CTE may read the other CTEs
        probe.set("with_", tree.args["with_"].copy())
    keys = [r[-1] for r in run_query(db_path, probe.sql(dialect="sqlite"), end + 1, timeout_s).rows]
    return any(0 < cut < len(keys) and same_value(keys[cut - 1], keys[cut]) for cut in (start, end))


def row_number_cuts_tie(db_path: Path, tree: exp.Expression, sql: str, timeout_s: float) -> bool:
    """ROW_NUMBER() numbers tied rows anyway, so `rn = 1` drops all but one. With RANK() they stay, so a
    tie at the cut-off shows up as extra rows."""
    ranked = tree.copy()
    windows = [w for w in ranked.find_all(exp.Window) if isinstance(w.this, exp.RowNumber)]
    if not windows:
        return False
    for window in windows:
        window.set("this", exp.Anonymous(this="RANK"))
        if order := window.args.get("order"):
            order.set("expressions", order.expressions[:1])
    counts = [run_query(db_path, f"SELECT COUNT(*) FROM ({q})", 1, timeout_s).rows[0][0]
              for q in (sql, ranked.sql(dialect="sqlite"))]
    return counts[0] != counts[1]


def arbitrary_rows(db_path: Path, sql: str, timeout_s: float) -> str | None:
    """Why the result rests on an arbitrary choice between rows, if it does: with Compressor-02 and -03 both
    at 8 h, `ORDER BY hours DESC LIMIT 1` names one of them and the pair teaches the model to hide the other.
    A correlated LIMIT cannot run on its own, so it counts as an SQL error."""
    tree = sqlglot.parse_one(sql, read="sqlite")
    for select in tree.find_all(exp.Select):
        columns = [c.unalias() for c in select.selects]     # SQLite fills a bare column from one MAX row
        if (not select.args.get("group") and any(isinstance(c, (exp.Max, exp.Min)) for c in columns)
                and any(isinstance(c, exp.Column) for c in columns)):
            return "bare column beside MIN/MAX"
        if select.args.get("limit"):
            if not select.args.get("order"):
                return "LIMIT without ORDER BY"
            if limit_cuts_tie(db_path, tree, select, timeout_s):
                return "hides a tie"
    return "hides a tie" if row_number_cuts_tie(db_path, tree, sql, timeout_s) else None


def mixes_units(sql: str) -> bool:
    """True if readings or peak values are aggregated or ranked across sensor types. The number means
    nothing (rpm dwarfs degC), so the top row is always a fast sensor, whatever the machine does."""
    tree = sqlglot.parse_one(sql, read="sqlite")
    for join in tree.find_all(exp.Join):                    # every join to sensors mentions sensor_id
        join.set("on", None)
    if any(c.name in ONE_UNIT for c in tree.find_all(exp.Column)):
        return False
    for node in tree.find_all(exp.Avg, exp.Sum, exp.Min, exp.Max, exp.Ordered):
        if not any(c.name in MEASURED for c in node.find_all(exp.Column)) or node.find(exp.Case):
            continue                                        # SUM(CASE ... THEN 1) is a count
        if not any(c.name.endswith("_threshold") for d in node.find_all(exp.Div)
                   for c in d.expression.find_all(exp.Column)):    # value / threshold has no unit
            return True
    return False


def check_pair(db_path: Path, question: str, sql: str, timeout_s: float) -> tuple[str | None, str | None]:
    """(cleaned SQL, None) for a usable query, else (None, reason)."""
    sql = " ".join(sql.split())
    if re.search(r"'now'|CURRENT_(?:TIMESTAMP|DATE|TIME)", sql, re.IGNORECASE):
        return None, "uses the real clock"       # would drift away from the data's "now"
    if re.search(r"quality\b[^,)]*'good'", sql, re.IGNORECASE) and not ASKS_FOR_QUALITY.search(question):
        return None, "unasked quality filter"    # drops 'uncertain' readings (eval/candidate_tasks.md, Step 6)
    if "--" in sql or "/*" in sql:
        return None, "has a comment"
    if '"' in sql:                               # SQLite reads an unknown "name" as a string, not an error
        return None, "double quotes"
    try:
        sql = validate_sql(sql)
        rows = run_query(db_path, sql, 2, timeout_s).rows
        if not rows or (len(rows) == 1 and all(c in (None, 0) for c in rows[0])):
            return None, "empty result"          # a wrong filter also gives 0 / NULL, so it proves nothing
        if mixes_units(sql):
            return None, "mixes units"
        reason = arbitrary_rows(db_path, sql, timeout_s)
    except SQLValidationError:
        return None, "failed the validator"
    except QueryTimeoutError:
        return None, "timeout"
    except sqlite3.Error:
        return None, "SQL error"
    return (None, reason) if reason else (sql, None)


def actual_category(category: str, sql: str) -> str:
    pattern = CATEGORY_SQL.get(category)
    return OFF_CATEGORY if pattern and not re.search(pattern, sql, re.IGNORECASE) else category


def sql_skeleton(sql: str) -> str:
    """The SQL without what a paraphrase changes: literal values, aliases and table qualifiers."""
    tree = sqlglot.parse_one(sql, read="sqlite")
    for lit in list(tree.find_all(exp.Literal)):
        lit.replace(exp.Placeholder())
    for col in tree.find_all(exp.Column):
        col.set("table", None)
    for table in tree.find_all(exp.Table):
        table.set("alias", None)
    for alias in list(tree.find_all(exp.Alias)):
        alias.replace(alias.this)
    return tree.sql(dialect="sqlite").lower()


def in_test_split(sql: str, percent: int = TEST_PERCENT) -> bool:
    return int(hashlib.sha1(sql_skeleton(sql).encode()).hexdigest(), 16) % 100 < percent


def query_fingerprint(db_path: Path, sql: str, timeout_s: float = 30.0) -> tuple:
    """Same tables, literals and result rows: the same query, even with the joins written in another order,
    which sql_skeleton cannot see. The result alone is not enough: many questions have the answer 'Mazak'."""
    tree = sqlglot.parse_one(sql, read="sqlite")
    tables = {t.name.lower() for t in tree.find_all(exp.Table)} - {c.alias.lower() for c in tree.find_all(exp.CTE)}
    literals = {lit.this.lower() for lit in tree.find_all(exp.Literal)}
    rows = run_query(db_path, sql, FINGERPRINT_ROWS, timeout_s).rows
    cells = sorted(sorted(repr(round(c, 6) if isinstance(c, float) else c) for c in row) for row in rows)
    return tuple(sorted(tables)), tuple(sorted(literals)), repr(cells)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def thinnest_category(rows: list[dict], rng: random.Random) -> str:
    counts = Counter(r["category"] for r in rows)
    fewest = min(counts[c] for c in CATEGORIES)
    return rng.choice([c for c in CATEGORIES if counts[c] == fewest])


def generate(pool: ModelPool, db_path: Path, schema: str, raw_path: Path = RAW_PATH,
             rejected_path: Path = REJECTED_PATH, target: int = TARGET_PAIRS, max_requests: int = 30,
             timeout_s: float = 10.0, pause_s: float = PAUSE_S, rng: random.Random | None = None) -> Counter:
    """Ask for batches until `target` pairs are kept, `max_requests` are spent or all models are blocked.
    Returns what happened to the pairs in this run (`kept` and each reject reason)."""
    rng = rng or random.Random()
    values = db_values(db_path)
    rows = read_jsonl(raw_path)
    seen = {question_key(r["question"]) for r in rows}
    outcome: Counter = Counter()

    for i in range(max_requests):
        if len(rows) >= target:
            break
        if i and pause_s:
            time.sleep(pause_s)
        category = thinnest_category(rows, rng)
        same = [r["question"] for r in rows if r["category"] == category]
        prompt = batch_prompt(schema, values, category, rng.choice(STYLES), rng.choice(FOCI),
                              rng.sample(same, min(len(same), 15)))
        try:
            msg = pool.invoke(prompt)
        except AllModelsUnavailable as e:
            print(f"stopping: {e}")
            break
        except Exception as e:  # noqa: BLE001 -- the failed model is blocked; the next round tries another
            log.warning("batch %d failed: %s", i + 1, str(e)[:200])
            outcome["failed request"] += 1
            continue

        model = msg.response_metadata.get("model_name", "unknown")
        pairs = parse_batch(msg.text)
        if not pairs:
            outcome["unparsable reply"] += 1
        kept, rejected = [], []
        for pair in pairs:
            key = question_key(pair["question"])
            sql, reason = ((None, "duplicate question") if key in seen
                           else check_pair(db_path, pair["question"], pair["sql"], timeout_s))
            if reason:
                outcome[reason] += 1
                rejected.append({"category": category, "model": model, "reason": reason, **pair})
                continue
            seen.add(key)
            kept.append({"question": pair["question"].strip(), "sql": sql,
                         "category": actual_category(category, sql), "model": model})
        rows += kept
        outcome["kept"] += len(kept)
        append_jsonl(raw_path, kept)
        append_jsonl(rejected_path, rejected)
        print(f"[{i + 1}/{max_requests}] {category:16} {model:24} kept {len(kept):2}/{len(pairs):2}   "
              f"total {len(rows)}/{target}", flush=True)
    return outcome


def recheck(db_path: Path, raw_path: Path = RAW_PATH, rejected_path: Path = REJECTED_PATH,
            timeout_s: float = 10.0) -> Counter:
    """Run the current check_pair over the kept pairs and move the failures to rejected.jsonl."""
    kept, rejected = [], []
    for row in read_jsonl(raw_path):
        _, reason = check_pair(db_path, row["question"], row["sql"], timeout_s)
        if reason:
            rejected.append({**row, "reason": reason})
        else:
            kept.append(row)
    append_jsonl(rejected_path, rejected)
    raw_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    return Counter({"kept": len(kept)}) + Counter(r["reason"] for r in rejected)


def write_splits(schema: str, db_path: Path, raw_path: Path = RAW_PATH, out_dir: Path = DATA_DIR) -> tuple[int, int]:
    """train.jsonl and test.jsonl with the exact prompt the model gets at inference time. A test pair that
    repeats a training query is dropped, not moved: the model has effectively seen it already."""
    splits: dict[str, list[dict]] = {"train": [], "test": []}
    for r in read_jsonl(raw_path):
        side = "test" if in_test_split(r["sql"]) else "train"
        splits[side].append({"prompt": build_prompt(schema, r["question"]), "completion": r["sql"], **r})
    seen = {query_fingerprint(db_path, r["sql"]) for r in splits["train"]}
    splits["test"] = [r for r in splits["test"] if query_fingerprint(db_path, r["sql"]) not in seen]
    out_dir.mkdir(parents=True, exist_ok=True)
    for side, rows in splits.items():
        path = out_dir / f"{side}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(splits["train"]), len(splits["test"])


def print_stats(raw_path: Path = RAW_PATH, rejected_path: Path = REJECTED_PATH) -> None:
    rows, rejected = read_jsonl(raw_path), read_jsonl(rejected_path)
    train, test = (len(read_jsonl(raw_path.parent / f"{side}.jsonl")) for side in ("train", "test"))
    print(f"{len(rows)} pairs ({train} train, {test} test, {len(rows) - train - test} test repeats dropped), "
          f"{len({sql_skeleton(r['sql']) for r in rows})} distinct SQL shapes")
    for title, counts in [("category", Counter(r["category"] for r in rows)),
                          ("model", Counter(r["model"] for r in rows)),
                          ("rejected", Counter(r["reason"] for r in rejected))]:
        print(f"\n{title}:")
        for name, n in counts.most_common():
            print(f"  {name:28} {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["generate", "split", "recheck", "stats"])
    ap.add_argument("--max-requests", type=int, default=30, help="requests this run (the UI needs quota too)")
    ap.add_argument("--target", type=int, default=TARGET_PAIRS)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    settings = get_settings()
    schema = load_schema(str(settings.schema_path))
    if args.command == "generate":
        from ida.llm import get_chat_model
        members = [(m, get_chat_model(m, temperature=TEMPERATURE))
                   for m in settings.planner_models if m != BENCHMARK_MODEL]
        outcome = generate(ModelPool("datagen", members, default_memory()), settings.db_path, schema,
                           target=args.target, max_requests=args.max_requests,
                           timeout_s=settings.query_timeout_s)
        print(dict(outcome.most_common()))
    elif args.command == "split":
        print("train {}, test {}".format(*write_splits(schema, settings.db_path)))
    elif args.command == "recheck":
        print(dict(recheck(settings.db_path, timeout_s=settings.query_timeout_s).most_common()))
    else:
        print_stats()


if __name__ == "__main__":
    main()
