"""Measure search_manuals retrieval quality on eval/retrieval_queries.yaml.

Usage:
    python eval/eval_retrieval.py            # prints recall@1, recall@3, MRR and misses
"""
from pathlib import Path

import yaml

from ida.mcp_server.manuals_index import ManualIndex

QUERIES = Path(__file__).with_name("retrieval_queries.yaml")


def is_hit(hit: dict, case: dict) -> bool:
    return ((case["machine_type"] is None or hit["machine_type"] == case["machine_type"])
            and any(hit["section"].startswith(s) for s in case["sections"]))


def evaluate(index: ManualIndex, use_filter: bool = False, k: int = 5) -> dict:
    cases = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))
    ranks, misses = [], []
    for case in cases:
        mtype = case["machine_type"] if use_filter else None
        hits = index.search(case["query"], machine_type=mtype, k=k)
        rank = next((i + 1 for i, h in enumerate(hits) if is_hit(h, case)), None)
        ranks.append(rank)
        if rank is None or rank > 3:
            misses.append({"query": case["query"], "rank": rank,
                           "got": [f"{h['machine_type']} / {h['section']}" for h in hits[:3]]})
    n = len(cases)
    return {"n": n,
            "recall@1": sum(r == 1 for r in ranks) / n,
            "recall@3": sum(r is not None and r <= 3 for r in ranks) / n,
            "mrr": sum(1 / r for r in ranks if r) / n,
            "misses": misses}


if __name__ == "__main__":
    from ida.config import get_settings
    s = get_settings()
    index = ManualIndex(s.index_dir, s.manuals_dir)
    for use_filter in (False, True):
        r = evaluate(index, use_filter)
        print(f"filter={use_filter!s:5}  n={r['n']}  recall@1={r['recall@1']:.2f}  "
              f"recall@3={r['recall@3']:.2f}  MRR={r['mrr']:.2f}")
        for m in r["misses"]:
            print(f"   miss (rank {m['rank']}): {m['query']!r} -> {m['got']}")
