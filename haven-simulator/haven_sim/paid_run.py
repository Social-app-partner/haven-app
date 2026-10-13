"""Opt-in, resumable, budget-constrained OpenAI simulation of entirely fictional Haven sessions.

No API call occurs without --execute. Ledger records reservations BEFORE HTTP calls.
Local USD estimates are safeguards, NOT a substitute for provider-side spending limits.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import json
import os
from pathlib import Path
import random
from typing import Any

from .content import make_personas, make_posts
from .decisions import ACTIONS, SAFE_REPLIES, heuristic_action
from .evaluation import audit_items
from .feed_api import HavenFeedClient
from .models import SimulationConfig
from .ranking import offline_feed_items
from .safety import eligible, moderation_check

INSTRUCTIONS = (
    "You simulate the preferences of ONE completely fictional adult using Haven, a peer-support "
    "social app with no like or follower counts. This is fictional software testing, not a "
    "prediction of real human behavior or a mental-health assessment. "
    "Respond with ONLY a JSON object having an 'actions' array. Each action object has ONLY "
    "'post_id' and 'action'. For each listed post, choose exactly one action from "
    "skip, hug, save, hide, report, reply. Preserve post order. "
    "Hug means support, not viral popularity. Do not give medical advice, write comments, "
    "invent posts or users, include commentary, infer diagnoses, or maximize screen time."
)
TRAIN_COLUMNS = (
    "user_id", "post_id", "session_id", "position", "action", "label", "post_mood_chip",
    "fictional_user_mood", "topic", "rank_score", "policy", "decision_source",
)


def usd(value: Any, name: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {name}: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return amount


@dataclass(frozen=True)
class PaidConfig:
    users: int = 100
    posts: int = 100
    sessions_per_user: int = 3
    feed_size: int = 8
    seed: int = 42
    feed_source: str = "offline"
    api_url: str = "http://localhost:4000"
    model: str = ""
    input_rate: str = ""
    output_rate: str = ""
    cached_input_rate: str = ""  # empty means normal input rate (no assumed discount)
    budget_usd: str = "100"
    reserve_usd: str = "5"
    max_calls: int = 50000
    max_output_tokens: int = 600

    def validate(self, *, execute: bool = False) -> None:
        SimulationConfig(users=self.users, posts=self.posts,
                         sessions_per_user=self.sessions_per_user, feed_size=self.feed_size,
                         seed=self.seed, feed_source=self.feed_source, api_url=self.api_url).validate()
        if self.max_calls < 1:
            raise ValueError("--max-calls must be at least 1")
        if not 160 <= self.max_output_tokens <= 16000:
            raise ValueError("--max-output-tokens must be between 160 and 16000")
        budget, reserve = usd(self.budget_usd, "budget"), usd(self.reserve_usd, "reserve")
        if budget <= reserve:
            raise ValueError("--budget-usd must exceed --reserve-usd")
        if self.model and any(c.isspace() for c in self.model):
            raise ValueError("OpenAI model ID cannot include whitespace")
        if execute:
            if not self.model:
                raise ValueError("Set OPENAI_MODEL or --model to an available API model ID")
            if not self.input_rate or not self.output_rate:
                raise ValueError("Specify current verified --price-input and --price-output rates (USD per 1M tokens)")
            if usd(self.input_rate, "input_rate") <= 0 or usd(self.output_rate, "output_rate") <= 0:
                raise ValueError("Input and output prices must be positive")
            if self.cached_input_rate:
                usd(self.cached_input_rate, "cached_input_rate")

    def fingerprint(self) -> dict:
        """Fields that may NOT change when resuming paid sessions."""
        return {k: v for k, v in asdict(self).items()
                if k not in ("budget_usd", "reserve_usd", "max_calls")}


def make_prompt(persona, posts) -> str:
    # Absolutely no personally identifying information or any clinical/journal data.
    payload = {
        "fictional_test_only": True,
        "persona": {
            "interests": persona.interests,
            "preferred_style": persona.preferred_style,
            "fictional_voluntary_mood": persona.user_mood,
        },
        "feed": [
            {"post_id": post.post_id, "topic": post.topic,
             "post_mood_chip": post.mood_chip, "text": post.text[:240]}
            for post in posts
        ],
        "expected_actions": len(posts),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def upper_reservation(prompt: str, max_output_tokens: int, input_rate: Decimal,
                      output_rate: Decimal) -> Decimal:
    """Generous planning estimate, not a provable USD billing ceiling.

    UTF-8 byte count plus token accounting overhead and 2x padding is conservative
    for this short, text-only workload. Model-specific tokenization can vary.
    """
    conservative_input = len((prompt + INSTRUCTIONS).encode("utf-8")) + 2048
    return 2 * ((Decimal(conservative_input) * input_rate +
                 Decimal(max_output_tokens + 128) * output_rate) / Decimal(1_000_000))


def calculate_cost(usage: dict, input_rate: Decimal, output_rate: Decimal,
                   cached_rate: Decimal | None = None) -> Decimal:
    inp = int(usage.get("input_tokens", 0))
    out = int(usage.get("output_tokens", 0))
    cached = int(usage.get("cached_input_tokens", 0))
    if min(inp, out, cached) < 0 or cached > inp:
        raise ValueError("Provider returned invalid token usage")
    discounted = input_rate if cached_rate is None else cached_rate
    return ((Decimal(inp - cached) * input_rate + Decimal(cached) * discounted +
             Decimal(out) * output_rate) / Decimal(1_000_000))


def validate_decisions(raw: str, expected_ids: list[str]) -> dict[str, str]:
    """Reject malformed, duplicated, missing and extra output; never silently fabricate LLM labels."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Model output was not valid JSON") from exc
    if not isinstance(obj, dict) or set(obj) != {"actions"} or not isinstance(obj["actions"], list):
        raise ValueError("Model output must contain only an actions array")
    actions = obj["actions"]
    if len(actions) != len(expected_ids):
        raise ValueError("Model did not return exactly one decision for each post")
    seen: dict[str, str] = {}
    for entry in actions:
        if not isinstance(entry, dict) or set(entry) != {"post_id", "action"}:
            raise ValueError("Invalid action object")
        pid, action = entry["post_id"], entry["action"]
        if not isinstance(pid, str) or pid in seen or action not in ACTIONS:
            raise ValueError("Unknown, duplicate or invalid action")
        seen[pid] = action
    if set(seen) != set(expected_ids):
        raise ValueError("Model returned unknown or missing post IDs")
    return seen


class OpenAIResponsesTransport:
    """Only imported/instantiated after the user explicitly supplies --execute."""

    def __init__(self, model: str):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY not set; configure a dedicated OpenAI API project")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the SDK: py -3 -m pip install -r requirements-openai.txt") from exc
        self.model = model
        # No automatic retries: even a timeout could have been charged.
        self.client = OpenAI(max_retries=0, timeout=90.0)

    def create(self, prompt: str, max_output_tokens: int) -> tuple[str, dict]:
        response = self.client.responses.create(
            model=self.model,
            instructions=INSTRUCTIONS,
            input=prompt,
            max_output_tokens=max_output_tokens,
            store=False,
        )
        u = response.usage
        if u is None:
            raise RuntimeError("Response lacked token usage: treat this request as potentially billed")
        detail = getattr(u, "input_tokens_details", None)
        usage = {
            "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None),
            "cached_input_tokens": getattr(detail, "cached_tokens", 0) if detail else 0,
        }
        if usage["input_tokens"] is None or usage["output_tokens"] is None:
            raise RuntimeError("Provider usage missing tokens: treat request as potentially billed")
        return response.output_text or "", usage


def append_ledger(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_ledger(path: Path) -> tuple[dict, dict]:
    reservations: dict[str, dict] = {}
    settled: dict[str, dict] = {}
    if not path.exists():
        return reservations, settled
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                raise RuntimeError(f"Empty ledger line {number}; inspect file before resuming")
            try:
                e = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Corrupted ledger line {number}; do not erase possible API usage") from exc
            sid = e.get("session_id")
            if e.get("kind") == "reserve":
                if sid in reservations:
                    raise RuntimeError(f"Duplicate paid reservation for session {sid}")
                reservations[sid] = e
            elif e.get("kind") == "result":
                if sid not in reservations or sid in settled:
                    raise RuntimeError(f"Orphan/duplicate paid result for {sid}")
                settled[sid] = e
            elif e.get("kind") == "recover":
                if sid not in reservations or (sid in settled and settled[sid]["status"] in ("ok", "recovered")):
                    raise RuntimeError(f"Unexpected recovery for {sid}")
                settled[sid] = {**settled.get(sid, {}), "status": "recovered", "actions": e["actions"],
                                "feed": e["feed"], "fictional_user_mood": e["fictional_user_mood"]}
            else:
                raise RuntimeError(f"Unknown ledger entry on line {number}")
    return reservations, settled


def committed_cost(reservations: dict, results: dict) -> Decimal:
    total = Decimal(0)
    for sid, reservation in reservations.items():
        result = results.get(sid)
        if result and result.get("cost_usd") is not None:
            total += usd(result["cost_usd"], "ledger cost")
        else:
            # Unknown costs are treated as reserved, never zeroed out.
            total += usd(reservation["reserved_usd"], "ledger reservation")
    return total


def paid_session_id(user_index: int, session_index: int) -> str:
    return f"s{user_index:07d}_{session_index:04d}"


def _mood_transition(persona, seed: int, user_index: int, session_index: int) -> None:
    rng = random.Random(seed * 881 + user_index * 100003 + session_index * 137)
    if rng.random() >= .24:
        return
    moods = ("great", "good", "okay", "low", "struggling")
    new = max(0, min(4, moods.index(persona.user_mood) + rng.choice((-1, 1))))
    persona.user_mood = moods[new]
    persona.consecutive_low_days = (min(10, persona.consecutive_low_days + 1)
                                    if persona.user_mood in ("low", "struggling") else 0)


def _update_persona(persona, items: list[dict], actions: dict[str, str], post_map: dict) -> None:
    for item in items:
        if item.get("type") != "post":
            continue
        pid = item["id"]
        post = post_map[pid]
        action = actions[pid]
        if action in ("hide", "report"):
            persona.affinity[post.topic] = max(.05, persona.affinity[post.topic] - .08)
        elif action in ("hug", "save"):
            persona.affinity[post.topic] = min(1., persona.affinity[post.topic] + .015)
        persona.recently_seen.append(pid)
        persona.recently_seen = persona.recently_seen[-100:]
        persona.last_topic = post.topic


def _write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _export(output_dir: Path, cfg: PaidConfig, personas, posts, records,
            reservations, results, stop_reason, api_requests: int, all_completed: bool) -> dict:
    """Rebuild derived data from journal. Safe to run repeatedly after an interruption."""
    post_map = {p.post_id: p for p in posts}
    _write_jsonl(output_dir / "posts.jsonl", (asdict(p) for p in posts))
    _write_jsonl(output_dir / "personas.jsonl", (asdict(p) for p in personas))
    actions_count, moods, violations = Counter(), Counter(), Counter()
    counts = Counter()
    moderate = Counter(moderation_check(p)[1] for p in posts)
    with (output_dir / "training_interactions.csv").open("w", encoding="utf-8", newline="") as train, \
         (output_dir / "events.jsonl").open("w", encoding="utf-8") as ev, \
         (output_dir / "moderation_queue.jsonl").open("w", encoding="utf-8") as mod:
        writer = csv.DictWriter(train, fieldnames=TRAIN_COLUMNS)
        writer.writeheader()
        for post in posts:
            if not post.is_approved:
                mod.write(json.dumps({"post_id": post.post_id, "synthetic": True,
                                      "reason": "not_approved", "status": "pending_human_review"}) + "\n")
        for record in records:
            sid, user_id = record["session_id"], record["user_id"]
            items, decisions = record["feed"], record["actions"]
            source = record["source"]
            counts["sessions"] += 1
            if source == "openai":
                counts["llm_sessions"] += 1
            else:
                counts["heuristic_recoveries"] += 1
            counts["extra_care_cards"] += sum(x.get("type") == "extra-care-card" for x in items)
            for issue in record["audit_violations"]:
                violations[issue] += 1
            for position, item in enumerate(items, 1):
                if item.get("type") != "post":
                    continue
                pid = item["id"]
                post = post_map[pid]
                action = decisions[pid]
                label = 1 if action in ("hug", "save") else (0 if action in ("hide", "report") else None)
                score = item.get("score", {}).get("total")
                event = {
                    "event_id": f"{sid}_i{position:03d}", "session_id": sid,
                    "user_id": user_id, "post_id": pid, "post_author_id": post.author_id,
                    "position": position, "event_type": "impression_action", "action": action,
                    "explicit_action_label": label, "post_mood_chip": post.mood_chip,
                    "fictional_user_mood": record["fictional_user_mood"], "rank_score": score,
                    "policy": cfg.feed_source, "decision_source": source, "is_synthetic": True,
                    "reply_text": (SAFE_REPLIES[(position + len(sid)) % len(SAFE_REPLIES)]
                                   if action == "reply" else None),
                }
                ev.write(json.dumps(event, ensure_ascii=False) + "\n")
                writer.writerow({
                    "user_id": user_id, "post_id": pid, "session_id": sid, "position": position,
                    "action": action, "label": "" if label is None else label,
                    "post_mood_chip": post.mood_chip, "fictional_user_mood": record["fictional_user_mood"],
                    "topic": post.topic, "rank_score": score, "policy": cfg.feed_source,
                    "decision_source": source,
                })
                counts["impressions"] += 1
                actions_count[action] += 1
                moods[post.mood_chip] += 1
                if action == "report":
                    mod.write(json.dumps({"post_id": pid, "session_id": sid, "synthetic": True,
                                          "reason": "fictional_user_report",
                                          "status": "pending_human_review"}) + "\n")
            ev.write(json.dumps({"event_id": f"{sid}_end", "session_id": sid, "user_id": user_id,
                                 "event_type": "session_end", "reason": "finite_feed_completed",
                                 "wellbeing_prompt_shown": True, "is_synthetic": True}) + "\n")
            counts["wellbeing_prompts"] += 1
    spent = committed_cost(reservations, results)
    used = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    for result in results.values():
        for k in used:
            used[k] += int((result.get("usage") or {}).get(k, 0) or 0)
    report = {
        "name": "Haven synthetic OpenAI simulation (v0.3)",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True,
        "not_clinically_validated": True,
        "not_real_user_performance": True,
        "not_a_trained_model": True,
        "status": "complete" if all_completed else "partial",
        "stop_reason": stop_reason,
        "algorithm_under_test": "Haven /api/feed" if cfg.feed_source == "api" else "Python offline approximation",
        "configuration": {**asdict(cfg), "openai_api_key": "NEVER STORED"},
        "counts": dict(counts),
        "actions": dict(actions_count),
        "mood_chip_impressions": dict(moods),
        "initial_content_moderation": dict(moderate),
        "safety_audit": {"passed": not violations, "violation_counts": dict(violations)},
        "openai": {
            "model": cfg.model, "attempted_calls": len(reservations),
            "successful_calls": sum(r.get("status") == "ok" for r in results.values()),
            "unresolved_or_failed_calls": sum(r.get("status") != "ok" for r in results.values()) +
                                          (len(reservations) - len(results)),
            "usage": used,
            "estimated_committed_usd": str(spent),
            "configured_budget_usd": cfg.budget_usd,
            "unspent_reserve_usd": cfg.reserve_usd,
            "remaining_local_allowance_usd": str(max(Decimal(0), usd(cfg.budget_usd, "budget") -
                                                        usd(cfg.reserve_usd, "reserve") - spent)),
            "pricing_is_user_supplied_not_live_verified": True,
        },
        "haven_backend_requests_this_execution": api_requests,
        "caution": ("Local estimated costs are not enforceable provider billing limits. Set a separate "
                    "OpenAI project-level budget/alerts. Synthetic labels don't indicate safety, "
                    "mental health improvement or real preference validity."),
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output_dir / "REPORT.md").write_text(
        "# Haven paid synthetic simulation\n\n"
        f"**Status:** {report['status']} ({stop_reason})\n\n"
        f"- Completed sessions: {counts['sessions']}\n"
        f"- Synthetic impressions: {counts['impressions']}\n"
        f"- API attempts across restarts: {len(reservations)}\n"
        f"- Estimated committed API cost: **${spent}**\n"
        f"- Remaining under local limit (excluding reserve): "
        f"${report['openai']['remaining_local_allowance_usd']}\n"
        f"- Mechanical policy violations: {dict(violations)}\n\n"
        "Provider billing may differ; never treat synthetic interactions as proof of wellbeing.\n",
        encoding="utf-8",
    )
    return report


def preview_plan(cfg: PaidConfig) -> dict:
    cfg.validate()
    rng = random.Random(cfg.seed)
    person = make_personas(1, rng)[0]
    posts = make_posts(max(cfg.posts, 2), rng)
    # A realistic first sample, not a guaranteed cost or throughput prediction.
    available = [p for p in posts if eligible(p, person)[0]][:cfg.feed_size]
    rate_in = usd(cfg.input_rate, "input_rate") if cfg.input_rate else Decimal("0.10")
    rate_out = usd(cfg.output_rate, "output_rate") if cfg.output_rate else Decimal("0.50")
    sample_prompt = make_prompt(person, available)
    per_call = upper_reservation(sample_prompt, cfg.max_output_tokens, rate_in, rate_out)
    approx_input = (len((sample_prompt + INSTRUCTIONS).encode("utf-8")) // 4) + 100
    approx_output = 30 * len(available) + 50
    approx_cost = calculate_cost({"input_tokens": approx_input,
                                  "output_tokens": approx_output,
                                  "cached_input_tokens": 0}, rate_in, rate_out)
    total_calls = min(cfg.max_calls, cfg.users * cfg.sessions_per_user)
    return {
        "mode": "DRY_RUN_NO_API_CALLS", "model": cfg.model or "SET_OPENAI_MODEL",
        "fictional_sessions_requested": cfg.users * cfg.sessions_per_user,
        "calls_per_session": 1, "maximum_attempts": min(cfg.max_calls, cfg.users * cfg.sessions_per_user),
        "budget_usd": cfg.budget_usd, "reserve_usd": cfg.reserve_usd,
        "assumed_input_usd_per_1m": str(rate_in), "assumed_output_usd_per_1m": str(rate_out),
        "sample_padded_reservation_usd_per_call": str(per_call),
        "very_rough_sample_call_cost_usd": str(approx_cost),
        "very_rough_total_call_cost_usd_before_caps": str(approx_cost * total_calls),
        "planning_note": ("Default rates are historical screenshot examples, not live-verified prices. "
                          "Actual calls require explicit prices, --execute and API key. "
                          "The per-call reserve is a conservative planning estimate, not a billing guarantee."),
    }


def run_paid(cfg: PaidConfig, output_dir: Path, *, transport=None, skip_failed: bool = False,
             execute: bool = False, progress_every: int = 25) -> dict:
    cfg.validate(execute=True)
    if progress_every < 0:
        raise ValueError("progress_every must be non-negative")
    if not execute:
        raise ValueError("Paid run requires explicit --execute; use plan-openai to preview")
    if transport is None and not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Set OPENAI_API_KEY before --execute; no API calls have been made")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "paid_run_config.json"
    immutable = cfg.fingerprint()
    signature = hashlib.sha256(json.dumps(immutable, sort_keys=True).encode()).hexdigest()
    if config_path.exists():
        previous = json.loads(config_path.read_text(encoding="utf-8"))
        if previous.get("fingerprint") != signature:
            raise RuntimeError("Existing paid run has different settings/model/prices. Use a NEW output folder.")
    else:
        # Fail closed if a user points to a non-empty unrelated output folder.
        if any(output_dir.iterdir()):
            raise RuntimeError("Output directory already contains files. Choose a clean run directory.")
        config_path.write_text(json.dumps({"fingerprint": signature, "immutable": immutable}, indent=2), encoding="utf-8")

    ledger_path = output_dir / "api_ledger.jsonl"
    reservations, settled = read_ledger(ledger_path)
    rate_in, rate_out = usd(cfg.input_rate, "price_input"), usd(cfg.output_rate, "price_output")
    cached_rate = usd(cfg.cached_input_rate, "price_cached") if cfg.cached_input_rate else None
    ceiling = usd(cfg.budget_usd, "budget") - usd(cfg.reserve_usd, "reserve")
    if committed_cost(reservations, settled) > ceiling:
        raise RuntimeError("Previously committed cost exceeds requested local budget minus reserve")

    rng = random.Random(cfg.seed)
    personas = make_personas(cfg.users, rng)
    posts = make_posts(cfg.posts, rng)
    post_map = {post.post_id: post for post in posts}
    client = HavenFeedClient(cfg.api_url) if cfg.feed_source == "api" else None
    if client is not None and not client.health().get("ok"):
        raise RuntimeError("Haven backend health check failed")
    # SDK initialization itself is offline; verify dependencies BEFORE reserving any requests.
    # The injected fake transport enables completely network-free unit tests.
    used_transport = transport if transport is not None else OpenAIResponsesTransport(cfg.model)
    records = []
    stop_reason = "completed"
    finished_all = True
    for index, persona in enumerate(personas):
        for session_index in range(cfg.sessions_per_user):
            sid = paid_session_id(index, session_index)
            if session_index:
                _mood_transition(persona, cfg.seed, index, session_index)
            existing = settled.get(sid)
            reservation = reservations.get(sid)
            if existing is not None and existing["status"] in ("ok", "recovered"):
                items, decisions = existing["feed"], existing["actions"]
                source = "openai" if existing["status"] == "ok" else "heuristic_recovery"
            elif reservation is not None:
                # Could have reached OpenAI even if the process crashed / timed out.
                if not skip_failed:
                    raise RuntimeError(
                        f"Session {sid} has a failed/in-flight request. No duplicate call made. "
                        "Review provider usage, then rerun with --skip-failed to use explicitly "
                        "labeled heuristic fallback; conservative reserve remains charged locally."
                    )
                if existing and existing["status"] == "ok":
                    raise RuntimeError("Unexpected state")
                allowed = [p for p in posts if eligible(p, persona)[0]]
                items = (client.rank_session(persona, posts, cfg.feed_size, 720 + session_index * 24)["items"]
                         if client is not None else offline_feed_items(persona, posts, cfg.feed_size,
                                                                      720 + session_index * 24))
                mock_rng = random.Random(cfg.seed + 700000 + index * 1000 + session_index)
                decisions = {x["id"]: heuristic_action(persona, post_map[x["id"]], mock_rng)
                             for x in items if x.get("type") == "post"}
                recover = {"kind": "recover", "session_id": sid, "feed": items,
                           "actions": decisions, "fictional_user_mood": persona.user_mood}
                append_ledger(ledger_path, recover)
                settled[sid] = {**(existing or {}), "status": "recovered", "actions": decisions,
                                "feed": items, "fictional_user_mood": persona.user_mood}
                source = "heuristic_recovery"
            else:
                if len(reservations) >= cfg.max_calls:
                    stop_reason, finished_all = "max_calls", False
                    break
                allowed = [p for p in posts if eligible(p, persona)[0]]
                items = (client.rank_session(persona, posts, cfg.feed_size, 720 + session_index * 24)["items"]
                         if client is not None else offline_feed_items(persona, posts, cfg.feed_size,
                                                                      720 + session_index * 24))
                audit = audit_items(items, allowed, mood=persona.user_mood,
                                    low_days=persona.consecutive_low_days,
                                    show_sensitive=persona.show_sensitive, top_n=cfg.feed_size)
                if audit:
                    raise RuntimeError(f"Safety audit failed for {sid}: {audit}. No OpenAI call made.")
                feed_posts = [post_map[x["id"]] for x in items if x.get("type") == "post"]
                if not feed_posts:
                    stop_reason, finished_all = "empty_feed", False
                    break
                prompt = make_prompt(persona, feed_posts)
                reserved = upper_reservation(prompt, cfg.max_output_tokens, rate_in, rate_out)
                if committed_cost(reservations, settled) + reserved > ceiling:
                    stop_reason, finished_all = "local_budget_limit", False
                    break
                reservation = {"kind": "reserve", "session_id": sid, "reserved_usd": str(reserved),
                               "model": cfg.model, "max_output_tokens": cfg.max_output_tokens,
                               "timestamp_utc": datetime.now(timezone.utc).isoformat()}
                append_ledger(ledger_path, reservation)
                reservations[sid] = reservation
                try:
                    raw, usage = used_transport.create(prompt, cfg.max_output_tokens)
                except Exception as exc:
                    append_ledger(ledger_path, {"kind": "result", "session_id": sid,
                                                "status": "failed", "error_type": type(exc).__name__})
                    settled[sid] = {"status": "failed"}
                    raise RuntimeError(
                        f"OpenAI request failed for {sid} ({type(exc).__name__}). "
                        "Ledger reserves possible costs. No automatic retry."
                    ) from exc
                try:
                    cost = calculate_cost(usage, rate_in, rate_out, cached_rate)
                    decisions = validate_decisions(raw, [p.post_id for p in feed_posts])
                except (ValueError, TypeError, KeyError) as exc:
                    # Unusable output may still be billable. Missing/invalid usage keeps reservation.
                    try:
                        billed_cost = str(calculate_cost(usage, rate_in, rate_out, cached_rate))
                    except (ValueError, TypeError, KeyError):
                        billed_cost = None
                    invalid = {"kind": "result", "session_id": sid, "status": "invalid",
                               "usage": usage, "cost_usd": billed_cost, "error_type": type(exc).__name__}
                    append_ledger(ledger_path, invalid)
                    settled[sid] = invalid
                    raise RuntimeError(f"OpenAI returned invalid decisions for {sid}; cost preserved, no retry") from exc
                result = {"kind": "result", "session_id": sid, "status": "ok",
                          "usage": usage, "cost_usd": str(cost), "feed": items,
                          "actions": decisions, "fictional_user_mood": persona.user_mood}
                append_ledger(ledger_path, result)
                settled[sid] = result
                source = "openai"
            allowed = [p for p in posts if eligible(p, persona)[0]]
            audit = audit_items(items, allowed, mood=persona.user_mood,
                                low_days=persona.consecutive_low_days,
                                show_sensitive=persona.show_sensitive, top_n=cfg.feed_size)
            if audit:
                raise RuntimeError(f"Replay audit failed for {sid}: {audit}; inspect ledger")
            _update_persona(persona, items, decisions, post_map)
            records.append({"session_id": sid, "user_id": persona.user_id, "feed": items,
                            "actions": decisions, "fictional_user_mood": persona.user_mood,
                            "source": source, "audit_violations": audit})
            if progress_every and len(records) % progress_every == 0:
                print(f"[Haven] sessions={len(records)}/{cfg.users * cfg.sessions_per_user} "
                      f"API attempts={len(reservations)} "
                      f"estimated committed USD=${committed_cost(reservations, settled)}",
                      flush=True)
        if not finished_all:
            break
    return _export(output_dir, cfg, personas, posts, records, reservations, settled,
                   stop_reason, client.calls if client else 0, finished_all)
