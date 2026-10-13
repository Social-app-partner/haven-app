"""Mock policy for synthetic fixtures only; no clinical or moderation guarantees."""
import re

# Matches the concept of resource flagging; a support-seeking post is *not*
# automatically removed. Real crisis assessment needs expert human review.
SUPPORT_RESOURCE_PATTERNS = [
    re.compile(r"\b(suicide|suicidal|self[- ]harm|kill myself|end my life|want to die)\b", re.I),
]


def needs_support_resource(text: str) -> bool:
    return any(pattern.search(text) for pattern in SUPPORT_RESOURCE_PATTERNS)


def needs_human_review(text: str) -> bool:
    """Legacy alias: flag for extra attention, not an automatic takedown."""
    return needs_support_resource(text)


def moderation_check(post):
    if not post.is_approved:
        return False, "not_approved"
    # For evaluation, assume approved fictional posts already passed moderation.
    # Crisis wording alone does NOT cause suppressive ranking.
    return True, "approved"


def eligible(post, persona):
    allowed, reason = moderation_check(post)
    if not allowed:
        return False, reason
    if post.author_id in persona.blocked_authors:
        return False, "blocked_author"
    if post.topic in persona.muted_topics:
        return False, "muted_topic"
    if set(post.content_notes) & set(persona.muted_content_notes):
        return False, "muted_content_note"
    return True, "eligible"
