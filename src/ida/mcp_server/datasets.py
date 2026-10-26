"""Server-side cache of query results so run_python can read them without the LLM copying rows."""
import csv
import hashlib
import re
from pathlib import Path

_ID = re.compile(r"^ds_[0-9a-f]{10}$")


class DatasetStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, sql: str, columns: list[str], rows: list[tuple]) -> str:
        """Store rows as CSV; the id is a hash of the SQL so re-running a query reuses its file."""
        dataset_id = "ds_" + hashlib.sha256(sql.encode()).hexdigest()[:10]
        with open(self.path(dataset_id), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(columns)
            w.writerows(rows)
        return dataset_id

    def path(self, dataset_id: str) -> Path:
        if not _ID.match(dataset_id):
            raise ValueError(f"Invalid dataset id: {dataset_id!r}")
        return self.root / f"{dataset_id}.csv"

    def exists(self, dataset_id: str) -> bool:
        return self.path(dataset_id).exists()
