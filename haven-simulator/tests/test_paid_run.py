"""All paid-mode tests use fakes. No OpenAI SDK, API key, or billing required."""
import csv
from decimal import Decimal
import os
from unittest.mock import patch
import threading
from http.server import ThreadingHTTPServer
import json
import tempfile
import unittest
from pathlib import Path

from haven_sim.paid_run import (
    PaidConfig, preview_plan, run_paid, calculate_cost, validate_decisions,
    upper_reservation, read_ledger, committed_cost, append_ledger,
)


class FakeTransport:
    def __init__(self, *, fail_at=None, invalid_at=None):
        self.calls = 0
        self.fail_at = fail_at
        self.invalid_at = invalid_at

    def create(self, prompt, max_output_tokens):
        self.calls += 1
        if self.fail_at == self.calls:
            raise TimeoutError("Fake timeout, might have been billed")
        request = json.loads(prompt)
        if self.invalid_at == self.calls:
            return '{"actions":[]}', {"input_tokens": 500, "output_tokens": 75, "cached_input_tokens": 0}
        body = json.dumps({"actions": [
            {"post_id": p["post_id"], "action": "hug" if i % 3 == 0 else "skip"}
            for i, p in enumerate(request["feed"])
        ]})
        return body, {"input_tokens": 500, "output_tokens": 100, "cached_input_tokens": 0}


def config(**updates):
    props = dict(users=3, posts=35, sessions_per_user=2, feed_size=5,
                 model="fake-test-model", input_rate="0.10", output_rate="0.50",
                 max_calls=100, budget_usd="100", reserve_usd="5")
    props.update(updates)
    return PaidConfig(**props)


class PaidRunTests(unittest.TestCase):
    def test_plan_is_offline_no_auth(self):
        plan = preview_plan(config())
        self.assertEqual(plan["mode"], "DRY_RUN_NO_API_CALLS")
        self.assertEqual(plan["fictional_sessions_requested"], 6)
        self.assertGreater(Decimal(plan["sample_padded_reservation_usd_per_call"]), 0)

    def test_requires_explicit_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "--execute"):
                run_paid(config(), Path(tmp), transport=FakeTransport())

    def test_cost_with_cached_input(self):
        sample = {"input_tokens": 1000, "output_tokens": 100, "cached_input_tokens": 500}
        cost = calculate_cost(sample, Decimal("0.10"), Decimal("0.50"), Decimal("0.01"))
        self.assertEqual(cost, Decimal("0.000105"))
        normal = calculate_cost(sample, Decimal("0.10"), Decimal("0.50"))
        self.assertEqual(normal, Decimal("0.00015"))
        with self.assertRaises(ValueError):
            calculate_cost({"input_tokens": 5, "output_tokens": 1, "cached_input_tokens": 6},
                           Decimal(".1"), Decimal(".5"))

    def test_output_validator_exact_actions(self):
        self.assertEqual(validate_decisions('{"actions":[{"post_id":"a","action":"hug"}]}', ["a"]), {"a": "hug"})
        for bad in ('{"actions":[]}', '{"actions":[{"post_id":"x","action":"hug"}]}',
                    '{"actions":[{"post_id":"a","action":"like"}]}',
                    '{"actions":[{"post_id":"a","action":"hug"},{"post_id":"a","action":"hug"}]}'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_decisions(bad, ["a"])

    def test_complete_and_resume_does_not_recharge(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "paid"
            fake = FakeTransport()
            first = run_paid(config(), folder, transport=fake, execute=True)
            self.assertEqual(first["status"], "complete")
            self.assertEqual(fake.calls, 6)
            self.assertEqual(first["counts"]["sessions"], 6)
            self.assertTrue(first["safety_audit"]["passed"])
            second_fake = FakeTransport()
            second = run_paid(config(), folder, transport=second_fake, execute=True)
            self.assertEqual(second_fake.calls, 0)
            self.assertEqual(second["counts"], first["counts"])
            self.assertEqual(second["openai"]["estimated_committed_usd"],
                             first["openai"]["estimated_committed_usd"])
            with (folder / "training_interactions.csv").open(newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertTrue(rows)
            self.assertTrue(all(r["decision_source"] == "openai" for r in rows))
            self.assertEqual(len(rows), second["counts"]["impressions"])
            text = (folder / "personas.jsonl").read_text().lower()
            self.assertNotIn("diagnosis", text)
            self.assertNotIn("journal", text)

    def test_max_calls_and_resume_increment(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "paid"
            fake = FakeTransport()
            first = run_paid(config(max_calls=2), folder, transport=fake, execute=True)
            self.assertEqual(first["status"], "partial")
            self.assertEqual(first["stop_reason"], "max_calls")
            self.assertEqual(fake.calls, 2)
            fake2 = FakeTransport()
            second = run_paid(config(max_calls=6), folder, transport=fake2, execute=True)
            self.assertEqual(second["status"], "complete")
            self.assertEqual(fake2.calls, 4)
            reservations, settled = read_ledger(folder / "api_ledger.jsonl")
            self.assertEqual(len(reservations), 6)
            self.assertEqual(len(settled), 6)

    def test_budget_stops_before_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport()
            result = run_paid(config(budget_usd="0.0051", reserve_usd="0.005"),
                              Path(tmp) / "paid", transport=fake, execute=True)
            self.assertEqual(fake.calls, 0)
            self.assertEqual(result["stop_reason"], "local_budget_limit")
            self.assertEqual(result["openai"]["attempted_calls"], 0)

    def test_bad_response_is_charged_and_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "paid"
            with self.assertRaisesRegex(RuntimeError, "invalid decisions"):
                run_paid(config(), folder, transport=FakeTransport(invalid_at=1), execute=True)
            reservations, settled = read_ledger(folder / "api_ledger.jsonl")
            self.assertEqual(len(reservations), 1)
            self.assertEqual(settled[next(iter(settled))]["status"], "invalid")
            self.assertGreater(committed_cost(reservations, settled), 0)
            with self.assertRaisesRegex(RuntimeError, "No duplicate call"):
                run_paid(config(), folder, transport=FakeTransport(), execute=True)
            fake = FakeTransport()
            report = run_paid(config(), folder, transport=fake, skip_failed=True, execute=True)
            self.assertEqual(report["status"], "complete")
            self.assertEqual(fake.calls, 5)
            self.assertEqual(report["counts"]["heuristic_recoveries"], 1)
            self.assertEqual(report["openai"]["successful_calls"], 5)
            # Recovery must persist; rerunning does not rebill.
            again = FakeTransport()
            run_paid(config(), folder, transport=again, execute=True)
            self.assertEqual(again.calls, 0)

    def test_timeout_reservation_stays_charged(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "paid"
            with self.assertRaisesRegex(RuntimeError, "No automatic retry"):
                run_paid(config(), folder, transport=FakeTransport(fail_at=1), execute=True)
            reservations, settled = read_ledger(folder / "api_ledger.jsonl")
            self.assertGreater(committed_cost(reservations, settled), 0)
            self.assertEqual(settled[next(iter(settled))]["status"], "failed")
            report = run_paid(config(), folder, transport=FakeTransport(),
                              skip_failed=True, execute=True)
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["counts"]["heuristic_recoveries"], 1)

    def test_no_api_key_fails_before_creating_paid_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "not_created"
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                    run_paid(config(), folder, execute=True)
            self.assertFalse(folder.exists())

    def test_reservation_only_crash_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "paid"
            # Leave a pristine signed config and one completed session, then mimic
            # power loss between reserve and result for the *next* session.
            run_paid(config(max_calls=1), folder, transport=FakeTransport(), execute=True)
            sid = "s0000000_0001"
            append_ledger(folder / "api_ledger.jsonl", {
                "kind": "reserve", "session_id": sid, "reserved_usd": "0.002",
                "model": "fake-test-model", "max_output_tokens": 600,
            })
            with self.assertRaisesRegex(RuntimeError, "No duplicate call"):
                run_paid(config(), folder, transport=FakeTransport(), execute=True)
            fake = FakeTransport()
            report = run_paid(config(), folder, transport=fake, execute=True,
                              skip_failed=True)
            self.assertEqual(fake.calls, 4)
            self.assertEqual(report["status"], "complete")
            reservations, results = read_ledger(folder / "api_ledger.jsonl")
            self.assertEqual(len(reservations), 6)
            self.assertEqual(results[sid]["status"], "recovered")
            self.assertGreaterEqual(committed_cost(reservations, results), Decimal("0.002"))

    def test_fake_paid_actions_against_mock_haven_feed(self):
        from test_haven import MockBackend
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockBackend)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake = FakeTransport()
                report = run_paid(config(users=2, sessions_per_user=2, posts=40,
                                         feed_source="api",
                                         api_url=f"http://127.0.0.1:{server.server_port}"),
                                  Path(tmp) / "paid", transport=fake, execute=True)
                self.assertEqual(report["status"], "complete")
                self.assertTrue(report["safety_audit"]["passed"])
                self.assertEqual(fake.calls, 4)
                self.assertEqual(report["haven_backend_requests_this_execution"], 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_configuration_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "paid"
            run_paid(config(max_calls=1), folder, transport=FakeTransport(), execute=True)
            with self.assertRaisesRegex(RuntimeError, "different settings"):
                run_paid(config(posts=50, max_calls=1), folder, transport=FakeTransport(), execute=True)


if __name__ == "__main__":
    unittest.main()
