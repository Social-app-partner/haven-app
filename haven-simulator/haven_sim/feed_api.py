"""Read-only integration with Haven's actual POST /api/feed contract.

POST body `posts` is a *synthetic fixture input*: it does not create posts
in the backend's persistent/in-memory post store.
"""
from datetime import datetime, timezone, timedelta
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .safety import eligible


class HavenAPIError(RuntimeError):
    pass


def haven_post(post, age_hours=0, now=None):
    """Adapt synthetic content into server/src/types.ts Post exactly.

    No user mood notes, journals, health histories or real identifiers.
    """
    now = now or datetime.now(timezone.utc)
    data = {
        "id": post.post_id,
        "author": post.author_id,
        "avatarColor": "#7FA8C9",
        "createdAt": (now - timedelta(hours=max(0, age_hours))).isoformat().replace("+00:00", "Z"),
        "title": post.text[:55],
        "body": post.text,
        "hugs": int(post.hugs),
        "replies": int(post.replies),
    }
    if post.mood_chip:
        data["moodChip"] = post.mood_chip
    if post.content_warning:
        data["contentWarning"] = post.content_warning
    return data


class HavenFeedClient:
    def __init__(self, base_url="http://localhost:4000", timeout=10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.calls = 0

    def health(self):
        try:
            with urlopen(f"{self.base_url}/api/health", timeout=self.timeout) as response:
                return json.load(response)
        except (OSError, ValueError) as exc:
            raise HavenAPIError(f"Haven backend unavailable at {self.base_url}: {exc}") from exc

    def feed(self, posts, *, user_mood=None, consecutive_low_days=0, show_sensitive=False, top_n=10):
        payload = {
            "posts": posts,
            "consecutiveLowDays": int(consecutive_low_days),
            "showSensitive": bool(show_sensitive),
            "topN": int(top_n),
        }
        if user_mood is not None:
            payload["userMood"] = user_mood
        req = Request(
            f"{self.base_url}/api/feed", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        self.calls += 1
        try:
            with urlopen(req, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise HavenAPIError(f"Haven returned HTTP {exc.code}: {detail}") from exc
        except (URLError, ValueError, OSError) as exc:
            raise HavenAPIError(f"Haven feed request failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise HavenAPIError("Invalid Haven feed response: missing items list")
        return result

    def rank_session(self, persona, posts, feed_size, session_hour, *, now=None):
        # Personal blocking / muting / approvals currently MUST be enforced before
        # hitting backend; the backend FeedContext has no blocked/muted fields.
        candidates = [post for post in posts if eligible(post, persona)[0]]
        request_posts = [haven_post(p, session_hour - p.created_hour, now) for p in candidates]
        return self.feed(
            request_posts, user_mood=persona.user_mood,
            consecutive_low_days=persona.consecutive_low_days,
            show_sensitive=persona.show_sensitive, top_n=feed_size,
        )
