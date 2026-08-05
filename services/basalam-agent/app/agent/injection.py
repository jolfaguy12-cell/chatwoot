"""Prompt-injection defenses for retrieved data.

Retrieved Hub text (product descriptions, page content, customer-entered
fields) is wrapped in <data> blocks that the system prompt declares to be
data-not-instructions, and obvious instruction-hijack markers are neutralized.
"""

import re

_TAG_RE = re.compile(r"<[^>]+>")
_INJECTION_PATTERNS = re.compile(
    r"(ignore (all )?(previous|above) instructions|disregard (the )?system prompt"
    r"|you are now|act as|jailbreak|<\s*/?\s*data\b[^>]*>)",
    re.IGNORECASE,
)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    out = re.sub(r"<(br|/p|/li|/h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    out = _TAG_RE.sub(" ", out)
    return re.sub(r"[ \t]+", " ", out).strip()


def neutralize(text: str | None) -> str:
    return _INJECTION_PATTERNS.sub("[removed]", text or "")


def wrap_data(source: str, text: str) -> str:
    safe_source = re.sub(r"[^a-z0-9_-]", "", source.lower())
    return f'<data source="{safe_source}">\n{neutralize(text)}\n</data>'
