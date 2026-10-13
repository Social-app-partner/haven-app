"""Safety-property checks for Haven feed API; not clinical effectiveness tests."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from .feed_api import HavenFeedClient
from .models import MOOD_CHIPS


def audit_items(items, eligible_posts, *, mood, low_days, show_sensitive, top_n):
    """Audits contract properties, never interprets scores as mental health benefit."""
    violations = []
    allowed = {p.post_id: p for p in eligible_posts}
    seen, author_count = set(), Counter()
    heavy = 0
    cards = []
    post_items = [item for item in items if item.get("type") == "post"]
    if len(post_items) > top_n:
        violations.append("top_n_exceeded")
    for index, item in enumerate(items):
        if item.get("type") == "extra-care-card":
            cards.append(index)
            continue
        if item.get("type") != "post":
            violations.append("unknown_item_type")
            continue
        pid = item.get("id")
        if pid in seen:
            violations.append("duplicate_post")
        seen.add(pid)
        if pid not in allowed:
            violations.append("unexpected_or_ineligible_post")
            continue
        post = allowed[pid]
        if not show_sensitive and post.content_warning:
            violations.append("content_warning_opt_out_violation")
        if item.get("author") != post.author_id:
            violations.append("author_mismatch")
        author_count[post.author_id] += 1
        if post.mood_chip == "Feeling heavy":
            heavy += 1
        score = item.get("score")
        if not isinstance(score, dict) or not isinstance(score.get("total"), (int, float)):
            violations.append("missing_score")
    if any(n > 2 for n in author_count.values()):
        violations.append("author_cap_exceeded")
    if mood in ("low", "struggling") and heavy > 2:
        violations.append("heavy_cap_exceeded")
    if low_days >= 3 and cards != [1]:
        violations.append("missing_or_misplaced_extra_care_card")
    if low_days < 3 and cards:
        violations.append("unexpected_extra_care_card")
    return sorted(set(violations))


def _fixture(pid, mood="Calm", *, author=None, warning=None, body="A gentle fictional post."):
    p = {
        "id": pid, "author": author or f"QuietMember-{pid}",
        "avatarColor": "#7FA8C9", "createdAt": datetime.now(timezone.utc).isoformat(),
        "title": f"Fictional {pid}", "body": body,
        "hugs": 0, "replies": 0, "moodChip": mood,
    }
    if warning:
        p["contentWarning"] = warning
    return p


def run_contract_checks(client: HavenFeedClient, output_dir: Path):
    """Send artificial test fixtures ONLY. Explicit fail checks for safety rules."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, posts, ctx, predicate, failure):
        try:
            response = client.feed(posts, **ctx)
            items = response["items"]
            okay = bool(predicate(items))
            results.append({"name": name, "passed": okay,
                            "details": "ok" if okay else failure,
                            "response_post_ids": [p.get("id") for p in items if p.get("type") == "post"]})
        except Exception as exc:
            results.append({"name": name, "passed": False,
                            "details": f"request_failed: {type(exc).__name__}: {exc}"})

    cw = [_fixture("cw", warning="Stress"), _fixture("plain")]
    check("content_warning_opt_out", cw, dict(show_sensitive=False),
          lambda items: "cw" not in [p.get("id") for p in items],
          "Sensitive post appeared without opt-in")
    check("content_warning_opt_in", cw, dict(show_sensitive=True),
          lambda items: "cw" in [p.get("id") for p in items],
          "Opted-in post was not eligible")

    mixed = [_fixture(f"heavy{i}", "Feeling heavy") for i in range(6)] + [
        _fixture(f"calm{i}", "Calm") for i in range(6)]
    check("heavy_cap_struggling", mixed,
          dict(user_mood="struggling", top_n=10),
          lambda items: sum(p.get("moodChip") == "Feeling heavy" for p in items) <= 2,
          "More than two heavy posts shown to struggling persona")

    authors = [_fixture(f"same{i}", author="RepeatAuthor") for i in range(6)] + [
        _fixture(f"other{i}") for i in range(6)]
    check("author_cap", authors, dict(top_n=10),
          lambda items: sum(p.get("author") == "RepeatAuthor" for p in items) <= 2,
          "Repeated author appeared more than twice")
    check("extra_care_after_3_low_days", mixed, dict(consecutive_low_days=3),
          lambda items: len(items) >= 2 and items[1].get("type") == "extra-care-card", 
          "Missing or misplaced extra-care card")
    check("no_unrequested_extra_care", mixed, dict(consecutive_low_days=2),
          lambda items: not any(p.get("type") == "extra-care-card" for p in items),
          "Extra-care card unexpectedly displayed")

    crisis = [_fixture("help", "Calm", body="I want to die; I am seeking support."), _fixture("normal")]
    check("crisis_resource_flag_without_suppression", crisis, dict(top_n=10),
          lambda items: any(p.get("id") == "help" and p.get("crisisResource") is True for p in items),
          "Support-seeking post absent or missing crisisResource flag")
    check("struggling_mood_prefers_hopeful", [
            _fixture("hopeful", "Hopeful"), _fixture("heavy", "Feeling heavy")],
          dict(user_mood="struggling", top_n=2),
          lambda items: [p.get("id") for p in items if p.get("type") == "post"][:1] == ["hopeful"],
          "Hopeful post did not outrank otherwise equal heavy post")
    report = {
        "synthetic_only": True, "not_clinically_validated": True,
        "endpoint": f"{client.base_url}/api/feed",
        "passed": sum(x["passed"] for x in results), "total": len(results),
        "all_passed": all(x["passed"] for x in results), "checks": results,
        "caution": "API contract checks are not moderation accuracy or clinical safety validation.",
    }
    (output_dir / "contract_checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
