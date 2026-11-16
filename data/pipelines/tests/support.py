"""파이프라인 테스트 공통 도우미.

conftest.py로 두지 않는다: API 테스트의 conftest와 모듈 이름이 겹친다. DB 테스트는 conn을 가져다 쓴다
(`from support import conn as conn`).
"""

import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not DB_URL, reason="TEST_DATABASE_URL이 없으면 DB 적재 테스트는 실행하지 않는다"
)


def make_feed(tmp_path: Path, files: dict[str, str]) -> Path:
    """파일 이름 → 내용으로 GTFS zip을 만든다."""
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


@pytest.fixture
def conn() -> Iterator[Connection]:
    """PostGIS 연결(TEST_DATABASE_URL, CI는 서비스 컨테이너). 한 테스트의 변경은 롤백한다."""
    assert DB_URL
    engine = create_engine(DB_URL)
    with engine.connect() as c:
        tx = c.begin()
        yield c
        tx.rollback()
    engine.dispose()
