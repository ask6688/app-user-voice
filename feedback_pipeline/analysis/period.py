"""Period helpers shared by the analysis pipeline and the report builder."""
from __future__ import annotations

import re
from datetime import date

PERIOD_IN_TITLE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-~—]\s*(\d{1,2})\.(\d{1,2})")


def parse_iso(text: str) -> date:
    return date.fromisoformat(text)


def period_label(start_text: str, end_text: str) -> str:
    """`2026-08-28` + `2026-09-03` -> `8.28-9.3`."""
    start = parse_iso(start_text)
    end = parse_iso(end_text)
    return f"{start.month}.{start.day}-{end.month}.{end.day}"


def period_dir(start_text: str, end_text: str) -> str:
    return f"{start_text}_{end_text}"


def chinese_period(start_text: str, end_text: str) -> str:
    start = parse_iso(start_text)
    end = parse_iso(end_text)
    return f"{start.month}月{start.day}日—{end.month}月{end.day}日"


def normalize_sheet_period(title: str) -> str | None:
    """Extract `8.24-8.30` from sheet names such as `小米（8.24-8.30）` or `OPPO_8.28-9.3`."""
    match = PERIOD_IN_TITLE.search(title or "")
    if not match:
        return None
    sm, sd, em, ed = match.groups()
    return f"{int(sm)}.{int(sd)}-{int(em)}.{int(ed)}"
