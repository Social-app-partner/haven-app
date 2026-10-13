"""Original curated fictional sample content; no scraping or real user data."""
import random
from .models import Persona, Post, TOPICS, MOOD_CHIPS

POST_TEMPLATES = {
    "grounding": [
        "A gentle reminder: notice something you can see, hear or touch.",
        "You can take your time today. A quiet pause can be enough.",
    ],
    "everyday_wins": [
        "I made time for a simple meal today. A small win for me.",
        "Getting through a difficult morning felt like progress today.",
    ],
    "creative_expression": [
        "I drew a tiny sunset today and wanted to share it.",
        "An easy sketch prompt: draw somewhere that feels peaceful.",
    ],
    "gentle_movement": [
        "A slow walk helped me notice the changing light.",
        "Rest is a valid choice too; movement is optional.",
    ],
    "community": [
        "You can join this conversation without explaining your whole story.",
        "What's a book, song, or hobby that brought you a little comfort?",
    ],
    "self_care": [
        "I set a small boundary and I'm giving myself credit.",
        "Taking a break from a screen can be a caring choice.",
    ],
    "mindfulness": [
        "A quiet minute with a cup of tea felt grounding today.",
        "There isn't one perfect way to have a mindful moment.",
    ],
    "resources": [
        "Peer support matters, and professional support is an option too.",
        "You can choose what topics you read and take a break at any time.",
    ],
}
TOPIC_CHIPS = {
    "grounding": "Calm", "everyday_wins": "Hopeful", "creative_expression": "Great",
    "gentle_movement": "Calm", "community": "Feeling heavy", "self_care": "Anxious",
    "mindfulness": "Calm", "resources": "Hopeful",
}


def make_personas(n: int, rng: random.Random):
    styles = ("quiet", "encouraging", "creative", "practical")
    moods = list(MOOD_CHIPS)
    people = []
    for i in range(n):
        interests = rng.sample(list(TOPICS), k=rng.randint(2, 4))
        muted = rng.sample([t for t in TOPICS if t not in interests], k=rng.choice([0, 0, 1]))
        notes = rng.choice([[], [], ["loss"], ["stress"]])
        mood = rng.choice(moods)
        affinity = {
            topic: round(min(1., rng.uniform(0.25, 0.7) + (0.25 if topic in interests else 0)), 3)
            for topic in TOPICS
        }
        people.append(Persona(
            user_id=f"u{i:07d}", interests=interests, muted_topics=muted,
            muted_content_notes=notes, blocked_authors=[], preferred_style=rng.choice(styles),
            affinity=affinity, user_mood=mood,
            consecutive_low_days=rng.choice([0, 1, 2, 3, 4, 5]) if mood in ("low", "struggling") else 0,
            show_sensitive=rng.random() < 0.1,
        ))
    return people


def make_posts(n: int, rng: random.Random):
    posts = []
    topics = list(POST_TEMPLATES)
    for i in range(n):
        topic = topics[i % len(topics)]
        # A content warning isn't proof that the post is harmful. It is a user choice.
        warning = "Stress" if i % 19 == 0 else ""
        posts.append(Post(
            post_id=f"p{i:08d}", author_id=f"QuietMember{rng.randrange(max(10, n // 12)):05d}",
            topic=topic, text=rng.choice(POST_TEMPLATES[topic]),
            support_quality=round(rng.uniform(0.65, 0.99), 3),
            mood_chip=TOPIC_CHIPS[topic], content_warning=warning,
            content_notes=["stress"] if warning else [],
            created_hour=rng.randrange(0, 30 * 24),
            hugs=rng.randrange(0, 12), replies=rng.randrange(0, 7),
        ))
    rng.shuffle(posts)
    return posts
