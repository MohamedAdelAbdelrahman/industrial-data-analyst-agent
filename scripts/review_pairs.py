"""Print training pairs with their real result rows for a hand review: a wrong answer often looks fine as SQL.

    python scripts/review_pairs.py                 # 10 tie-safe pairs, test split first
    python scripts/review_pairs.py --seed 2        # 10 others
"""
import argparse
import random
import re

from ida.config import get_settings
from ida.mcp_server.db import run_query
from ida.training_data import DATA_DIR, read_jsonl

TIE_SAFE = re.compile(r"\bRANK\(|=\s*\(\s*SELECT\s+(?:MAX|MIN)\(", re.IGNORECASE)
MAX_ROWS = 12


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-n", type=int, default=10)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    pairs = []
    for split in ("test", "train"):
        found = [r for r in read_jsonl(DATA_DIR / f"{split}.jsonl") if TIE_SAFE.search(r["sql"])]
        pairs += rng.sample(found, len(found))

    db_path = get_settings().db_path
    for i, pair in enumerate(pairs[:args.n], 1):
        result = run_query(db_path, pair["sql"], MAX_ROWS, 15)
        print(f"\n===== Pair {i} ({pair['category']}) =====")
        print(f"QUESTION: {pair['question']}")
        print(f"SQL:      {pair['sql']}")
        print(f"COLUMNS:  {result.columns}")
        for row in result.rows:
            print(f"  {row}")
        if result.truncated:
            print(f"  ... more than {MAX_ROWS} rows")


if __name__ == "__main__":
    main()
