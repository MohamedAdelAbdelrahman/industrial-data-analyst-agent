# Training data (not included)

The question→SQL pairs used to fine-tune the Text-to-SQL model are not in this repository. Gemini wrote them, and the Gemini API terms limit using its output to develop competing models, so the dataset and the trained model stay private.

The pipeline that made them is included, so you can rebuild a set of your own:

```bash
python data/generate_db.py --out data/sensors.db     # the database the pairs are checked against
python -m ida.training_data generate --max-requests 60   # needs GOOGLE_API_KEY; resumable
python -m ida.training_data split                    # train.jsonl / test.jsonl by SQL-shape hash
python -m ida.training_data stats
```

Files this creates here:

| File | Rows contain |
|---|---|
| `raw.jsonl` | `question`, `sql`, `category`, `model` (every kept pair) |
| `rejected.jsonl` | the same plus `reason` (failed an execution or rule check) |
| `train.jsonl`, `test.jsonl` | `prompt`, `completion` (the chat format used in training) plus the fields above |

What the checks are and why they exist: `src/ida/training_data.py` and the 6b rows of the decision log in [`docs/design.md`](../../docs/design.md). The final set had 1,518 pairs (1,380 train, 129 test).
