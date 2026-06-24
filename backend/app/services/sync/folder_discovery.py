from __future__ import annotations

from .constants import GUESS_PATTERNS, ROLE_CUSTOM, SPECIAL_USE_TO_ROLE


def classify_folder(imap_name: str, flags: list[str]) -> str:
    """Classify an IMAP folder name into a folder role.

    Priority:
    1) RFC 6154 SPECIAL-USE flags (e.g. \\Sent, \\Trash, \\Archive, \\Junk)
    2) Heuristic guessing by multilingual keywords in folder name
    3) Fallback to "custom"
    """

    for flag in flags:
        role = SPECIAL_USE_TO_ROLE.get(flag)
        if role:
            return role

    lower = imap_name.lower()
    for role, keywords in GUESS_PATTERNS.items():
        if any(keyword.lower() in lower for keyword in keywords):
            return role

    return ROLE_CUSTOM

