"""Offline fictional Haven actions. Paid OpenAI requests live in paid_run.py only."""
import random

ACTIONS = {"skip", "hug", "save", "hide", "report", "reply"}
SAFE_REPLIES = (
    "Thank you for sharing this.",
    "I appreciate this gentle reminder.",
    "Sending kindness your way.",
    "I hear you, and thank you for sharing.",
)


def heuristic_action(persona, post, rng: random.Random):
    """Toy behavior sampler, not empirical psychological or clinical modeling."""
    affinity = persona.affinity.get(post.topic, 0.4)
    enthusiasm = max(0., min(1., 0.65 * affinity + 0.35 * post.support_quality))
    r = rng.random()
    if r < .025 * (1 - enthusiasm):
        return "report" if r < .005 else "hide"
    if r < .65 - .28 * enthusiasm:
        return "skip"
    if r < .87:
        return "hug"  # solidarity, NOT a popularity objective
    if r < .94:
        return "save"
    if r < .985:
        return "reply"
    return "report"
