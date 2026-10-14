"""Tool results reshaped to fit the prompt where the raw answer would be cut before it is useful.

A doctype's definition is the one such result today: `get_document` on the `DocType` doctype
answers with the whole meta document, 80 000 characters for Lead, of which the prompt would
keep the first 8 000 and none of the fields. This keeps the fields and drops the rest.
"""

from __future__ import annotations

import json
import re
from typing import Any

META_DOCTYPE = "DocType"

# Fields that lay the form out and hold no data.
_LAYOUT_TYPES = frozenset(
    {"Section Break", "Column Break", "Tab Break", "HTML", "Heading", "Fold", "Button"}
)
_WITH_OPTIONS = frozenset({"Link", "Table", "Table MultiSelect", "Select", "Dynamic Link"})
_SELECT_SHOWN = 8


def condense_tool_result(name: str, args: dict[str, Any], result: str) -> str:
    """`result` as the prompt should see it; most results come back untouched."""
    if name == "get_document" and str(args.get("doctype") or "") == META_DOCTYPE:
        return condense_doctype(result)
    return result


def condense_doctype(result: str) -> str:
    """A DocType document as one line per data field: name, label, type, flags, options."""
    start = result.find("{")
    if start < 0:
        return result
    try:
        doc = json.loads(result[start:])
    except ValueError:
        return result
    if not isinstance(doc, dict) or not isinstance(doc.get("fields"), list):
        return result

    name = str(doc.get("name") or "")
    lines = [f"DocType {name}" + (f": {doc['description']}" if doc.get("description") else "")]
    naming = doc.get("autoname") or "hash"
    lines.append(
        f"naming: {naming}"
        + (f"; title field: {doc['title_field']}" if doc.get("title_field") else "")
        + ("; submittable" if doc.get("is_submittable") else "")
        + ("; child table" if doc.get("istable") else "")
    )
    required: list[str] = []
    lines.append("fields (fieldname | label | type | flags | options):")
    for f in doc["fields"]:
        if not isinstance(f, dict):
            continue
        fieldtype = str(f.get("fieldtype") or "")
        fieldname = str(f.get("fieldname") or "")
        if not fieldname or fieldtype in _LAYOUT_TYPES or f.get("hidden"):
            continue
        flags = []
        if f.get("reqd"):
            flags.append("required")
            required.append(fieldname)
        if f.get("read_only"):
            flags.append("read-only")
        if f.get("unique"):
            flags.append("unique")
        if f.get("fetch_from"):
            flags.append(f"fetched from {f['fetch_from']}")
        options = ""
        if fieldtype in _WITH_OPTIONS and f.get("options"):
            raw = str(f["options"])
            if fieldtype == "Select":
                values = [v for v in raw.split("\n") if v.strip()]
                shown = ", ".join(values[:_SELECT_SHOWN])
                options = shown + (
                    f", … ({len(values)} values)" if len(values) > _SELECT_SHOWN else ""
                )
            elif fieldtype == "Dynamic Link":
                options = f"doctype named in {raw}"
            else:
                options = raw
        if f.get("default") not in (None, ""):
            options = (options + "; " if options else "") + f"default {f['default']}"
        lines.append(
            f"- {fieldname} | {f.get('label') or ''} | {fieldtype}"
            + (f" | {', '.join(flags)}" if flags else " |")
            + (f" | {options}" if options else "")
        )
    lines.append("required: " + (", ".join(required) if required else "none"))
    return "\n".join(lines)


# What every document has whatever its definition says.
_STANDARD_FIELDS = frozenset(
    {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent",
     "parenttype", "parentfield", "doctype"}
)  # fmt: skip
_TABLE_TYPES = frozenset({"Table", "Table MultiSelect"})


def _doctype_document(result: str) -> dict[str, Any] | None:
    start = result.find("{")
    if start < 0:
        return None
    try:
        doc = json.loads(result[start:])
    except ValueError:
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("fields"), list) else None


def check_write_data(meta_result: str, doctype: str, data: Any) -> str | None:
    """What is wrong with the fields of a create or update, or None when they all exist.

    Frappe fails a write with an unknown field in one of three confusing ways, a 500 among
    them, so the agent says it first, in words the model can act on, with the fields that do
    exist. `meta_result` is the raw `get_document` answer for the DocType.
    """
    doc = _doctype_document(meta_result)
    if doc is None or not isinstance(data, dict):
        return None  # nothing to check it against; the write itself will say
    known: dict[str, str] = {}
    for f in doc["fields"]:
        if isinstance(f, dict) and f.get("fieldname"):
            known[str(f["fieldname"])] = str(f.get("fieldtype") or "")
    problems: list[str] = []
    unknown = [k for k in data if k not in known and k not in _STANDARD_FIELDS]
    if unknown:
        usable = [k for k, t in known.items() if t not in _LAYOUT_TYPES]
        problems.append(
            f"{doctype} has no field named {', '.join(repr(k) for k in unknown)}. "
            f"Its fields are: {', '.join(usable)}."
        )
    for key, value in data.items():
        if known.get(key) in _TABLE_TYPES and not isinstance(value, list):
            problems.append(
                f"{key} is a table field of {doctype}: it takes a list of row objects, "
                f"not {type(value).__name__}."
            )
    return " ".join(problems) or None


_URL_VALUE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_EMAIL_VALUE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCHEME_WWW = re.compile(r"https?://(www\.)?", re.IGNORECASE)


def _plain(text: str) -> str:
    """Lower case with scheme, www and trailing slashes gone, so the same address compares equal."""
    return _SCHEME_WWW.sub("", text.lower()).replace("/ ", " ").replace("/\n", "\n")


def unsupported_values(data: Any, evidence: str) -> list[tuple[str, str]]:
    """The (field, value) pairs of a write that are an address or email absent from `evidence`.

    A web address or an email cannot be worked out, only read, so one the agent never saw is one
    it made up. `evidence` is everything the model had in front of it: the question, the earlier
    turns, and every tool result of this turn.
    """
    if not isinstance(data, dict):
        return []
    haystack = _plain(evidence)
    found: list[tuple[str, str]] = []
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if _URL_VALUE.match(text):
            needle = _plain(text).rstrip("/")
        elif _EMAIL_VALUE.match(text):
            needle = text.lower()
        else:
            continue
        if needle not in haystack:
            found.append((str(key), text))
    return found
