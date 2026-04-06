from __future__ import annotations

import re
from difflib import SequenceMatcher


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_for_similarity(value: str) -> str:
    return _NORMALIZE_RE.sub(" ", value.lower()).strip()


def similarity_score(left: str, right: str) -> float:
    return SequenceMatcher(
        a=normalize_for_similarity(left),
        b=normalize_for_similarity(right),
    ).ratio()


def is_near_duplicate(candidate: str, existing: list[str], threshold: float) -> bool:
    return any(similarity_score(candidate, item) >= threshold for item in existing)
