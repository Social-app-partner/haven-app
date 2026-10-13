"""Format synthetic action sequences for future recommendation-model experiments.

Does NOT train a model. `save` is an explicit fictional preference action. Hugs
are excluded by default because they express support, not item desirability.
"""
from collections import defaultdict
import csv
import json
from pathlib import Path


def prepare_sequences(data_dir: Path, *, allowed_sources=("openai",), positive_actions=("save",)) -> dict:
    data_dir = Path(data_dir)
    source = data_dir / "training_interactions.csv"
    if not source.is_file():
        raise ValueError(f"Could not find {source}. Run paid simulation first.")
    per_user: dict[str, list[dict]] = defaultdict(list)
    impressions = 0
    with source.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            impressions += 1
            if row.get("decision_source") not in allowed_sources:
                continue
            if row.get("action") not in positive_actions:
                continue
            per_user[row["user_id"]].append(row)
    out = data_dir / "training_sequences.jsonl"
    stats = {"synthetic_only": True, "train": 0, "validation": 0, "test": 0,
             "users_with_saves": 0, "interactions_read": impressions,
             "positive_actions": list(positive_actions), "sources": list(allowed_sources)}
    with out.open("w", encoding="utf-8") as f:
        for user_id, rows in sorted(per_user.items()):
            rows.sort(key=lambda r: (r["session_id"], int(r["position"])))
            # For >=4 selected posts, use chronological last 2 as synthetic val/test.
            # Otherwise keep all items as training-only, since evaluation would be noisy.
            ids = [row["post_id"] for row in rows]
            if len(ids) >= 4:
                train, val, test = ids[:-2], ids[-2:-1], ids[-1:]
            else:
                train, val, test = ids, [], []
            record = {"user_id": user_id, "train_post_ids": train,
                      "validation_post_ids": val, "test_post_ids": test,
                      "synthetic_only": True}
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
            stats["users_with_saves"] += 1
            stats["train"] += len(train)
            stats["validation"] += len(val)
            stats["test"] += len(test)
    stats["warning"] = (
        "These holdouts measure only fit to AI-fabricated behavior; never report them as "
        "recommender performance on real users, or as mental-health safety evidence. "
        "Do not optimize hug totals, mood states or compulsive engagement."
    )
    (data_dir / "sequence_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats
