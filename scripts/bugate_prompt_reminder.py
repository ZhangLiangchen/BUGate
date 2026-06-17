#!/usr/bin/env python3
"""Lightweight prompt reminder for BUGate core.

The script is intentionally SUT-neutral. It only emits a reminder when the
prompt appears to ask for test implementation work.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any


KEYWORDS = re.compile(r"\b(test|pytest|case|e2e|implementation|code)\b|测试|用例|实现|自动化", re.I)


def extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(extract_text(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(extract_text(v) for v in value)
    return ""


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        text = extract_text(json.loads(raw))
    except json.JSONDecodeError:
        text = raw
    if KEYWORDS.search(text):
        sys.stderr.write("BUGate reminder: implementation work should follow the active SUT profile's pre-code gates.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
