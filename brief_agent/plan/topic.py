"""Topic validation and the normalised `topic_key` used to match cached plans."""

from __future__ import annotations

import re
import unicodedata

MAX_TOPIC_CHARS = 300


class TopicError(ValueError):
    pass


def validate_topic(topic: str) -> str:
    topic = " ".join((topic or "").split())
    if not topic:
        raise TopicError("topic is empty")
    if len(topic) > MAX_TOPIC_CHARS:
        raise TopicError(f"topic is {len(topic)} characters; keep it under {MAX_TOPIC_CHARS} (it's a topic, not a document)")
    return topic


def topic_key(topic: str) -> str:
    """'AI & Job-Losses!' → 'ai and job losses'."""
    t = unicodedata.normalize("NFKC", topic).lower()
    t = t.replace("&", " and ")
    t = re.sub(r"[-‐–—/_]", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())
