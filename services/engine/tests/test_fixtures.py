"""공통 픽스처(/engine-fixtures)를 Python 구현으로 다시 계산해 기대 결과와 같은지 본다.

같은 픽스처를 packages/engine-ts가 Jest로 돌린다. 엔진을 바꿨으면 scripts/make_fixtures.py로 다시 만든다.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from itinerary_engine.fixtures import FIXTURES_DIR, run

FIXTURES = FIXTURES_DIR


def fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def test_at_least_50_fixtures() -> None:
    assert len(fixtures()) >= 50  # PRD P2 완료 기준


@pytest.mark.parametrize("path", fixtures(), ids=lambda p: p.stem)
def test_fixture(path: Path) -> None:
    fixture: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert run(fixture["kind"], fixture["input"]) == fixture["expected"]
