"""Lexicon helpers: translate registered aliases (e.g. Chinese names) to core terms.

The heavy lifting happens in ``parser`` via token-level substitution; this
module owns the translation table construction and validation.
"""

from __future__ import annotations


def build_lexicon(raw: dict[str, str] | None) -> dict[str, str]:
    lex: dict[str, str] = {}
    for k, v in (raw or {}).items():
        key = str(k).strip()
        val = str(v).strip()
        if not key or not val:
            raise ValueError(f"bad lexicon entry: {k!r} = {v!r}")
        lex[key] = val
    return lex
