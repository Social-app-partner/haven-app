import csv
import json
import random
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from haven_sim.content import make_personas, make_posts
from haven_sim.models import SimulationConfig, Post, Persona, TOPICS
from haven_sim.ranking import recommend, offline_feed_items
from haven_sim.safety import eligible, needs_support_resource
from haven_sim.decisions import SAFE_REPLIES, ACTIONS
from haven_sim.simulator import simulate
from haven_sim.feed_api import haven_post, HavenFeedClient
from haven_sim.evaluation import audit_items, run_contract_checks


class HavenTests(unittest.TestCase):
    def test_support_seeking_not_automatically_hidden(self):
        person = make_personas(1, random.Random(4))[0]
        person.muted_topics = []
        post = Post(post_id="help", author_id="a", topic="community",
                    text="I want to die; I am seeking support.", support_quality=.8)
        self.assertTrue(needs_support_resource(post.text))
        self.assertTrue(eligible(post, person)[0])

    def test_muted_blocked_unapproved_never_in_feed(self):
        rng = random.Random(9)
        person = make_personas(1, rng)[0]
        posts = make_posts(50, rng)
        person.muted_topics = [posts[0].topic]
        person.blocked_authors = [posts[1].author_id]
        posts[2].is_approved = False
        ranked = recommend(person, posts, 20, 800)
        self.assertTrue(all(eligible(post, person)[0] for post, _ in ranked))
        self.assertTrue(all(post.topic != posts[0].topic for post, _ in ranked))
        self.assertTrue(all(post.author_id != posts[1].author_id for post, _ in ranked))
        self.assertNotIn(posts[2].post_id, [p.post_id for p, _ in ranked])

    def test_offline_caps_care_and_warnings(self):
        p = make_personas(1, random.Random(2))[0]
        p.user_mood, p.consecutive_low_days = "struggling", 4
        p.show_sensitive, p.muted_topics = False, []
        p.muted_content_notes, p.blocked_authors = [], []
        posts = make_posts(90, random.Random(3))
        items = offline_feed_items(p, posts, 10, 800)
        problems = audit_items(items, posts, mood=p.user_mood, low_days=4,
                               show_sensitive=False, top_n=10)
        self.assertEqual(problems, [])
        self.assertEqual(items[1]["type"], "extra-care-card")

    def test_haven_post_contract_excludes_private_information(self):
        post = Post(post_id="1", author_id="Anon", topic="community",
                    text="Hello", support_quality=.8, mood_chip="Hopeful",
                    content_warning="Grief", hugs=2, replies=1)
        obj = haven_post(post)
        self.assertEqual(set(obj), {"id", "author", "avatarColor", "createdAt",
                                    "title", "body", "hugs", "replies", "moodChip", "contentWarning"})
        self.assertNotIn("journal", json.dumps(obj).lower())

    def test_reproducible_offline(self):
        cfg = SimulationConfig(users=4, posts=32, sessions_per_user=2, feed_size=5)
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            simulate(cfg, a)
            simulate(cfg, b)
            for name in ("posts.jsonl", "events.jsonl", "training_interactions.csv", "personas.jsonl"):
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())

    def test_counts_actions_labels(self):
        cfg = SimulationConfig(users=10, posts=50, sessions_per_user=2, feed_size=6)
        with tempfile.TemporaryDirectory() as tmp:
            report = simulate(cfg, Path(tmp))
            self.assertEqual(report["counts"]["sessions"], 20)
            self.assertTrue(report["safety_audit"]["passed"])
            self.assertEqual(report["openai"]["calls"], 0)
            with (Path(tmp) / "training_interactions.csv").open(newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), report["counts"]["impressions"])
            self.assertTrue(all(row["label"] == "" for row in rows if row["action"] in ("skip", "reply")))
            self.assertTrue(all(row["action"] in ACTIONS for row in rows))
            events = [json.loads(s) for s in (Path(tmp) / "events.jsonl").read_text().splitlines()]
            self.assertTrue(all(e["is_synthetic"] for e in events))
            self.assertTrue(all(e.get("reply_text") in SAFE_REPLIES for e in events if e.get("action") == "reply"))
            self.assertEqual(sum(e["event_type"] == "session_end" for e in events), 20)
            self.assertNotIn("watch_seconds", (Path(tmp) / "events.jsonl").read_text())

    def test_paid_api_disallowed_in_legacy_simulate(self):
        with self.assertRaisesRegex(ValueError, "openai-run"):
            SimulationConfig(engine="openai").validate()

    def test_audit_detects_violation(self):
        post = Post(post_id="x", author_id="a", topic="grounding", text="hi", support_quality=.8,
                    content_warning="Stress")
        issues = audit_items([{"type": "post", "id": "x", "author": "a", "score": {"total": 1}}],
                             [post], mood="okay", low_days=0, show_sensitive=False, top_n=10)
        self.assertIn("content_warning_opt_out_violation", issues)


class MockBackend(BaseHTTPRequestHandler):
    """Mock API to test HTTP integration shape; NOT Haven's TypeScript implementation."""
    requests = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/api/health":
            body = json.dumps({"ok": True, "service": "mock-haven"}).encode()
            self.send_response(200)
        else:
            body = b'{}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.__class__.requests.append(data)
        persona = Persona(user_id="fake", interests=[], muted_topics=[], muted_content_notes=[],
                          blocked_authors=[], preferred_style="", affinity={},
                          user_mood=data.get("userMood") or "okay",
                          consecutive_low_days=data.get("consecutiveLowDays", 0),
                          show_sensitive=data.get("showSensitive", False))
        posts = []
        for p in data.get("posts", []):
            posts.append(Post(post_id=p["id"], author_id=p["author"],
                              topic="community", text=p["body"], support_quality=.8,
                              mood_chip=p.get("moodChip", "Calm"),
                              content_warning=p.get("contentWarning", ""),
                              hugs=p.get("hugs", 0), replies=p.get("replies", 0), created_hour=720))
        items = offline_feed_items(persona, posts, data.get("topN", 10), 720)
        response = json.dumps({"items": items, "count": len(items), "context": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class HttpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockBackend)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_real_http_contract_to_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = HavenFeedClient(self.url)
            self.assertTrue(client.health()["ok"])
            result = run_contract_checks(client, Path(tmp))
            self.assertEqual(result["total"], 8)
            self.assertTrue(result["all_passed"], result["checks"])
            self.assertTrue((Path(tmp) / "contract_checks.json").exists())

    def test_synthetic_simulation_using_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = SimulationConfig(users=4, posts=32, sessions_per_user=2,
                                      feed_size=6, feed_source="api", api_url=self.url)
            report = simulate(config, Path(tmp))
            self.assertTrue(report["safety_audit"]["passed"])
            self.assertEqual(report["haven_backend_requests"], 8)
            self.assertEqual(report["openai"]["calls"], 0)
            self.assertTrue(all("notes" not in req and "journal" not in req
                                for req in MockBackend.requests))


if __name__ == "__main__":
    unittest.main()
