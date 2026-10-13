"""Fiction-only data models aligned to Haven's current backend contract."""
from dataclasses import dataclass, field
from typing import Dict, List

TOPICS = (
    "grounding", "everyday_wins", "creative_expression", "gentle_movement",
    "community", "self_care", "mindfulness", "resources",
)
MOODS = ("great", "good", "okay", "low", "struggling")
MOOD_CHIPS = {
    "great": "Great", "good": "Hopeful", "okay": "Calm",
    "low": "Anxious", "struggling": "Feeling heavy",
}


@dataclass
class Persona:
    user_id: str
    interests: List[str]
    muted_topics: List[str]
    muted_content_notes: List[str]
    blocked_authors: List[str]
    preferred_style: str
    affinity: Dict[str, float]
    user_mood: str = "okay"  # fictional, optional check-in state, NOT a diagnosis
    consecutive_low_days: int = 0
    show_sensitive: bool = False
    recently_seen: List[str] = field(default_factory=list)
    last_topic: str = ""
    # No journal notes, diagnoses, medical history, phone numbers or identities.


@dataclass
class Post:
    post_id: str
    author_id: str
    topic: str
    text: str
    support_quality: float  # purely synthetic editorial placeholder
    mood_chip: str = "Calm"  # labels from Haven server/src/types.ts
    content_warning: str = ""
    content_notes: List[str] = field(default_factory=list)
    is_approved: bool = True
    created_hour: int = 0
    hugs: int = 0
    replies: int = 0


@dataclass
class SimulationConfig:
    users: int = 100
    posts: int = 250
    sessions_per_user: int = 3
    feed_size: int = 10
    seed: int = 42
    engine: str = "heuristic"
    max_api_calls: int = 0
    feed_source: str = "offline"  # real Haven backend available via --feed-source api
    api_url: str = "http://localhost:4000"

    def validate(self):
        if not 1 <= self.users <= 1_000_000:
            raise ValueError("users must be between 1 and 1,000,000")
        if not 2 <= self.posts <= 1_000_000:
            raise ValueError("posts must be between 2 and 1,000,000")
        if not 1 <= self.sessions_per_user <= 1000:
            raise ValueError("sessions_per_user must be between 1 and 1000")
        if not 1 <= self.feed_size <= 100:
            raise ValueError("feed_size must be between 1 and 100")
        if self.engine not in ("heuristic", "openai"):
            raise ValueError("engine must be heuristic or openai")
        if self.feed_source not in ("offline", "api"):
            raise ValueError("feed_source must be offline or api")
        if self.max_api_calls < 0:
            raise ValueError("max_api_calls cannot be negative")
        if self.engine == "openai":
            raise ValueError("Paid sessions must use `python -m haven_sim openai-run --execute` "
                             "for resumable USD budget accounting")
        if not self.api_url.startswith(("http://localhost:", "http://127.0.0.1:", "https://")):
            raise ValueError("api_url must be localhost or an explicitly secured HTTPS endpoint")
