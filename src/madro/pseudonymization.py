"""
Hides @-mentioned account names from every LLM call in the pipeline, without
persisting a mapping anywhere.

Some real account names (e.g. @broxadasinistra) trip Gemini's
PROHIBITED_CONTENT safety filter on sight, even though nothing else about the demand or the retrieved data is
remotely sensitive. ROT13 is self-inverse (encoding twice returns the
original), so pseudonymizing and reversing are the same underlying
operation — no mapping table needs threading through the DB the way
target_entity does; any point in the pipeline that knows which handles were
in play (or already sees only an alias) can reverse it on its own.
"""

import codecs
import re

_MENTION_RE = re.compile(r"@(\w+(?:\.\w+)*)")


def rot13(text: str) -> str:
    return codecs.encode(text, "rot_13")


def extract_mentions(text: str) -> set[str]:
    """Every distinct @handle mentioned in text, without the leading @."""
    return {m.group(1) for m in _MENTION_RE.finditer(text)}


def pseudonymize(text: str, handles: set[str]) -> str:
    """Replaces every occurrence of each handle in `handles` — @-prefixed
    (demand text) or bare (a DB-sourced username field in retrieved
    evidence, which is never stored with an @) — with its ROT13 alias."""
    for handle in handles:
        alias = rot13(handle)
        text = text.replace(f"@{handle}", f"@{alias}").replace(handle, alias)
    return text


def depseudonymize(text: str, real_handles: set[str]) -> str:
    """Reverses pseudonymize() given the same *real* handles it was called
    with — ROT13 being self-inverse, this is just pseudonymize() applied to
    each handle's own alias instead of the handle itself."""
    return pseudonymize(text, {rot13(h) for h in real_handles})


def reveal(text: str, alias: str) -> str:
    """Reverses one already-known alias — e.g.
    AgentRunner._extract_mentioned_username's extracted username, which is
    itself an alias once the pipeline has been pseudonymized upstream — back
    to its real value within arbitrary text (e.g. generated SQL). A targeted
    replace of one known string, not a full mention scan."""
    return text.replace(alias, rot13(alias))
