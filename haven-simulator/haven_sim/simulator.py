"""Run fully fictional Haven sessions, collecting safety audits and event logs."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import csv
import json
import random
from pathlib import Path

from .content import make_personas, make_posts
from .decisions import heuristic_action, SAFE_REPLIES
from .evaluation import audit_items
from .feed_api import HavenFeedClient
from .models import SimulationConfig
from .ranking import offline_feed_items
from .safety import eligible, moderation_check


def dump_jsonl(fh, obj):
    fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def _evolve_checkin(persona, rng):
    """Optional *fictional* daily state transition; never infer a diagnosis."""
    if rng.random() >= .24:
        return
    moods = ("great", "good", "okay", "low", "struggling")
    n = moods.index(persona.user_mood)
    persona.user_mood = moods[max(0, min(4, n + rng.choice((-1, 1))))]
    persona.consecutive_low_days = (
        min(10, persona.consecutive_low_days + 1)
        if persona.user_mood in ("low", "struggling") else 0
    )


def simulate(config: SimulationConfig, output_dir: Path):
    config.validate()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(config.seed)
    personas, posts = make_personas(config.users, rng), make_posts(config.posts, rng)
    llm = None  # Paid API use only via resumable openai-run with cost ledger.
    client = HavenFeedClient(config.api_url) if config.feed_source == "api" else None
    if client is not None:
        health = client.health()
        if health.get("ok") is not True:
            raise RuntimeError("The configured Haven backend failed its health check")

    counters, mood_impressions, action_counts, violations = Counter(), Counter(), Counter(), Counter()
    moderation_counts = Counter(moderation_check(p)[1] for p in posts)
    reference_hour = 30 * 24

    with (output_dir / "posts.jsonl").open("w", encoding="utf-8") as fh:
        for post in posts:
            dump_jsonl(fh, asdict(post))

    with (output_dir / "personas.jsonl").open("w", encoding="utf-8") as people_fh, \
         (output_dir / "events.jsonl").open("w", encoding="utf-8") as events_fh, \
         (output_dir / "training_interactions.csv").open("w", encoding="utf-8", newline="") as train_fh, \
         (output_dir / "moderation_queue.jsonl").open("w", encoding="utf-8") as review_fh:
        fields = ("user_id", "post_id", "session_id", "position", "action", "label",
                  "post_mood_chip", "fictional_user_mood", "topic", "rank_score", "policy")
        writer = csv.DictWriter(train_fh, fieldnames=fields)
        writer.writeheader()
        for post in posts:
            if not post.is_approved:
                dump_jsonl(review_fh, {"post_id": post.post_id, "reason": "unapproved_fixture",
                                       "status": "pending_human_review", "synthetic": True})

        for user_index, persona in enumerate(personas):
            for session_index in range(config.sessions_per_user):
                # The mood is volunteered by a *fictional* persona, not inferred from behavior.
                if session_index:
                    _evolve_checkin(persona, rng)
                session_id = f"s{user_index:07d}_{session_index:04d}"
                hour = reference_hour + session_index * 24
                allowed = [post for post in posts if eligible(post, persona)[0]]
                if client is None:
                    items = offline_feed_items(persona, posts, config.feed_size, hour)
                else:
                    result = client.rank_session(persona, posts, config.feed_size, hour)
                    items = result["items"]
                problems = audit_items(items, allowed, mood=persona.user_mood,
                                       low_days=persona.consecutive_low_days,
                                       show_sensitive=persona.show_sensitive, top_n=config.feed_size)
                for problem in problems:
                    violations[problem] += 1
                counters["sessions"] += 1
                if problems:
                    counters["sessions_with_policy_violations"] += 1
                care = [x for x in items if x.get("type") == "extra-care-card"]
                counters["extra_care_cards"] += len(care)
                impression_items = [x for x in items if x.get("type") == "post"]
                matching = {p.post_id: p for p in allowed}
                feed = [(matching[x["id"]], x.get("score", {}).get("total"))
                        for x in impression_items if x.get("id") in matching]
                decisions = {}
                for position, item in enumerate(items, 1):
                    if item.get("type") != "post":
                        continue
                    post = matching.get(item.get("id"))
                    if post is None:
                        # Already recorded as audit failure; never log ineligible posts.
                        continue
                    action = decisions.get(post.post_id) or heuristic_action(persona, post, rng)
                    if action == "report":
                        dump_jsonl(review_fh, {
                            "post_id": post.post_id, "status": "pending_human_review",
                            "reason": "fictional_user_report", "session_id": session_id,
                            "synthetic": True,
                        })
                    if action in ("hide", "report"):
                        persona.affinity[post.topic] = max(.05, persona.affinity[post.topic] - .08)
                    if action in ("hug", "save"):
                        persona.affinity[post.topic] = min(1., persona.affinity[post.topic] + .015)
                    persona.recently_seen.append(post.post_id)
                    persona.recently_seen = persona.recently_seen[-100:]
                    persona.last_topic = post.topic
                    # Labels represent fabricated explicit *actions*, never wellbeing outcomes.
                    label = 1 if action in ("hug", "save") else (0 if action in ("hide", "report") else None)
                    score = item.get("score", {}).get("total")
                    dump_jsonl(events_fh, {
                        "event_id": f"{session_id}_i{position:03d}", "session_id": session_id,
                        "user_id": persona.user_id, "post_id": post.post_id,
                        "post_author_id": post.author_id, "position": position,
                        "event_type": "impression_action", "action": action,
                        "explicit_action_label": label,
                        "post_mood_chip": post.mood_chip,
                        "fictional_user_mood": persona.user_mood,
                        "rank_score": score, "policy": config.feed_source,
                        "reply_text": rng.choice(SAFE_REPLIES) if action == "reply" else None,
                        "is_synthetic": True,
                    })
                    writer.writerow({
                        "user_id": persona.user_id, "post_id": post.post_id,
                        "session_id": session_id, "position": position,
                        "action": action, "label": "" if label is None else label,
                        "post_mood_chip": post.mood_chip, "fictional_user_mood": persona.user_mood,
                        "topic": post.topic, "rank_score": score, "policy": config.feed_source,
                    })
                    counters["impressions"] += 1
                    action_counts[action] += 1
                    mood_impressions[post.mood_chip] += 1
                dump_jsonl(events_fh, {
                    "event_id": f"{session_id}_end", "session_id": session_id,
                    "user_id": persona.user_id, "event_type": "session_end",
                    "reason": "finite_feed_completed", "wellbeing_prompt_shown": True,
                    "is_synthetic": True,
                })
                counters["wellbeing_prompts"] += 1
            dump_jsonl(people_fh, asdict(persona))

    report = {
        "name": "Haven synthetic feed evaluation (v0.2)",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True,
        "not_clinically_validated": True,
        "not_real_user_performance": True,
        "algorithm_under_test": "Haven /api/feed" if client else "Python offline approximation",
        "configuration": asdict(config),
        "counts": dict(counters),
        "actions": dict(action_counts),
        "mood_chip_impressions": dict(mood_impressions),
        "initial_content_moderation": dict(moderation_counts),
        "safety_audit": {"passed": not violations, "violation_counts": dict(violations)},
        "openai": {
            "enabled": llm is not None, "model": llm.model if llm else None,
            "calls": llm.calls if llm else 0,
            "input_tokens": llm.input_tokens if llm else 0,
            "output_tokens": llm.output_tokens if llm else 0,
            "max_api_calls": config.max_api_calls,
        },
        "haven_backend_requests": client.calls if client else 0,
        "warning": (
            "Synthetic persona reactions are not a validated outcome measure, and passing "
            "these mechanical guardrails does not guarantee anyone's safety or comfort. "
            "Human moderation, privacy reviews and opt-in real-world evaluation are needed."
        ),
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = [
        "# Haven synthetic evaluation report", "",
        f"- Ranking source: **{report['algorithm_under_test']}**",
        f"- Synthetic sessions: **{counters['sessions']}**",
        f"- Feed impressions: **{counters['impressions']}**",
        f"- Extra-care cards: **{counters['extra_care_cards']}**",
        f"- Mechanical guardrails: **{'PASS' if not violations else 'FAIL'}**",
        f"- OpenAI calls made: **{report['openai']['calls']}**", "",
        "## Audit violations", "",
    ]
    if violations:
        summary += [f"- {name}: {amount}" for name, amount in sorted(violations.items())]
    else:
        summary += ["None detected in the generated sessions."]
    summary += ["", "**Not evidence of clinical benefit or actual user safety.**", ""]
    (output_dir / "REPORT.md").write_text("\n".join(summary), encoding="utf-8")
    return report
