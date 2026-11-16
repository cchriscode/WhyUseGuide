"""API 입출력 모델의 공통 바탕(PRD §7: JSON 키는 camelCase)과 시각 변환. 라우터들이 같이 쓴다."""

from datetime import time
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


HHMM = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]


def to_time(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def hhmm(t: time) -> str:
    return t.strftime("%H:%M")
