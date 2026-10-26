"""일본 국민의 축일 (내각부 syukujitsu.csv, Shift_JIS)."""

import csv
import io
from datetime import date
from pathlib import Path


def parse_holidays(raw: bytes) -> dict[date, str]:
    """'2027/9/20,敬老の日' 형식 → {날짜: 이름}. 첫 줄은 머리글."""
    rows = csv.reader(io.StringIO(raw.decode("cp932")))
    next(rows)
    out: dict[date, str] = {}
    for row in rows:
        if len(row) < 2 or not row[0].strip():
            continue
        y, m, d = (int(x) for x in row[0].strip().split("/"))
        out[date(y, m, d)] = row[1].strip()
    return out


def load_holidays(path: Path) -> dict[date, str]:
    return parse_holidays(path.read_bytes())
