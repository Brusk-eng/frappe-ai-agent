"""A turn's transcript: the messages the model saw and wrote, stored and replayed as its memory.

Modelled on an event stream: each assistant reply keeps the sequence of its turn (the model's own
envelopes, the tool results fed back, the skill text, and its final reply), and later turns are
rebuilt from those entries instead of from the reply's text alone. Without them the model did not
know it had searched, or which records it had already created.

Stored entries are capped so a long page or report cannot make a row, or a prompt, unbounded:
each entry is cut to `ENTRY_CHARS`, and a turn keeps its first and last entries when it has more
than `MAX_ENTRIES`.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

ENTRY_CHARS = 3000
MAX_ENTRIES = 30
_HEAD = 2  # the first entries say what the turn set out to do; the rest is its latest state

# Written in place of a pending write's result: the call was shown to the user, not made.
PAUSED_NOTE = (
    "This change was not made in this turn: the user was asked to allow it. It ran only if a "
    "later tool result shows it."
)
# Opens a confirmed turn's transcript, whose user message is the agent's own and is not saved.
ALLOWED_NOTE = "The user allowed the change that was waiting for their confirmation."


def _role(message: BaseMessage) -> str | None:
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, HumanMessage):
        return "human"
    return None


def _cut(text: str) -> str:
    if len(text) <= ENTRY_CHARS:
        return text
    return text[:ENTRY_CHARS] + f"\n[cut to {ENTRY_CHARS} characters in this conversation's memory]"


def serialize(messages: list[BaseMessage]) -> str | None:
    """The transcript as stored JSON, or None when there is nothing to keep."""
    entries = [
        {"role": role, "content": _cut(str(m.content))}
        for m in messages
        if (role := _role(m)) is not None and str(m.content)
    ]
    if not entries:
        return None
    if len(entries) > MAX_ENTRIES:
        dropped = len(entries) - MAX_ENTRIES + 1
        marker = {"role": "human", "content": f"[{dropped} earlier steps of this turn not kept]"}
        entries = [*entries[:_HEAD], marker, *entries[-(MAX_ENTRIES - _HEAD - 1) :]]
    return json.dumps(entries, ensure_ascii=False)


def deserialize(raw: Any) -> list[BaseMessage]:
    """The stored transcript as messages; [] for anything unreadable (the reply's text is used)."""
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    out: list[BaseMessage] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
            continue
        content = _cut(entry["content"])  # a row may have been written by something else
        if entry.get("role") == "ai":
            out.append(AIMessage(content=content))
        elif entry.get("role") == "human":
            out.append(HumanMessage(content=content))
    return out


def size(messages: list[BaseMessage]) -> int:
    return sum(len(str(m.content)) for m in messages)
