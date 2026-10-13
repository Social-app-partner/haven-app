import csv
import json
import tempfile
import unittest
from pathlib import Path
from haven_sim.training_export import prepare_sequences


class TrainingExportTests(unittest.TestCase):
    def test_only_opted_ai_saves_with_chronological_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            rows = [
                {"user_id": "u1", "post_id": f"p{i}", "session_id": f"s{i:03d}",
                 "position": "1", "action": "save", "decision_source": "openai"}
                for i in range(6)
            ] + [
                {"user_id": "u1", "post_id": "p_bad", "session_id": "s008", "position": "1",
                 "action": "hug", "decision_source": "openai"},
                {"user_id": "u2", "post_id": "p_other", "session_id": "s009", "position": "1",
                 "action": "save", "decision_source": "heuristic_recovery"},
            ]
            with (p / "training_interactions.csv").open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=rows[0].keys())
                w.writeheader(); w.writerows(rows)
            stats = prepare_sequences(p)
            self.assertEqual(stats["interactions_read"], 8)
            self.assertEqual((stats["train"], stats["validation"], stats["test"]), (4, 1, 1))
            seq = [json.loads(x) for x in (p / "training_sequences.jsonl").read_text().splitlines()]
            self.assertEqual(len(seq), 1)
            self.assertEqual(seq[0]["train_post_ids"], ["p0", "p1", "p2", "p3"])
            self.assertEqual(seq[0]["test_post_ids"], ["p5"])
            self.assertTrue(seq[0]["synthetic_only"])
