"""Offline reference implementation of Haven's TypeScript ranker (not source of truth).

For real conformance use --feed-source api, which invokes the repository's
POST /api/feed rather than copying its policy assumptions.
"""
import math
from .models import MOOD_CHIPS
from .safety import eligible, needs_support_resource

SERVING_MATRIX = {
    "struggling": {"great": .35, "good": 1., "okay": .90, "low": .60, "struggling": .40},
    "low":        {"great": .50, "good": .90, "okay": .90, "low": .65, "struggling": .50},
    "okay":       {"great": .70, "good": .80, "okay": .85, "low": .70, "struggling": .60},
    "good":       {"great": .90, "good": .85, "okay": .75, "low": .80, "struggling": .85},
    "great":      {"great": .90, "good": .80, "okay": .70, "low": .85, "struggling": .90},
}
CHIP_TO_MOOD = {v: k for k, v in MOOD_CHIPS.items()}


def recommend(persona, posts, feed_size, hour, rng=None):
    """Mirror score, filtering, author/heavy caps and greedy diversity.

    Returns (post, score) pairs. The care card is injected by feed_items().
    No implicit health-condition inference or engagement maximization.
    """
    scored = []
    for post in posts:
        if not eligible(post, persona)[0]:
            continue
        if post.content_warning and not persona.show_sensitive:
            continue
        chip = CHIP_TO_MOOD.get(post.mood_chip)
        affinity = SERVING_MATRIX[persona.user_mood][chip] if chip else .6
        age = max(0, hour - post.created_hour)
        recency = 2 ** (-age / 12)
        warmth = math.log1p(post.hugs + post.replies) / math.log1p(50)
        scored.append((post, .5 * affinity + .2 * recency + .15 * warmth))
    counts_authors, counts_chips = {}, {}
    remaining, selected, heavy_count = scored, [], 0
    while remaining and len(selected) < feed_size:
        best_idx, best_score = -1, -math.inf
        for idx, (post, base) in enumerate(remaining):
            if counts_authors.get(post.author_id, 0) >= 2:
                continue
            if persona.user_mood in ("low", "struggling") and post.mood_chip == "Feeling heavy" and heavy_count >= 2:
                continue
            penalty = .15 * min(1., counts_authors.get(post.author_id, 0) * .5 + counts_chips.get(post.mood_chip, 0) * .25)
            score = base - penalty
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx < 0:
            break
        post, _ = remaining.pop(best_idx)
        selected.append((post, round(best_score, 8)))
        counts_authors[post.author_id] = counts_authors.get(post.author_id, 0) + 1
        counts_chips[post.mood_chip] = counts_chips.get(post.mood_chip, 0) + 1
        if post.mood_chip == "Feeling heavy":
            heavy_count += 1
    return selected


def offline_feed_items(persona, posts, feed_size, hour):
    """Shape analogous to POST /api/feed response: list of post/card dicts."""
    results = []
    for post, score in recommend(persona, posts, feed_size, hour):
        results.append({
            "type": "post", "id": post.post_id, "author": post.author_id,
            "moodChip": post.mood_chip, "contentWarning": post.content_warning or None,
            "crisisResource": needs_support_resource(post.text),
            "score": {"total": score},
        })
    if persona.consecutive_low_days >= 3:
        results.insert(1, {"type": "extra-care-card", "reason": "Fictional extra-care prompt"})
    return results
