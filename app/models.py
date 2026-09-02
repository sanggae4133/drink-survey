"""pydantic 스키마 — 메뉴 옵션 JSON의 형태 검증이 핵심."""
import json
from typing import List

from pydantic import BaseModel, Field, ValidationError


class OptionChoice(BaseModel):
    label: str = Field(min_length=1)
    delta: int = 0  # 가격 증감(원)


class OptionGroup(BaseModel):
    name: str = Field(min_length=1)      # 예: "온도"
    required: bool = False               # true면 응답 시 반드시 선택
    choices: List[OptionChoice] = Field(min_length=1)


OPTIONS_EXAMPLE = (
    '[{"name": "온도", "required": true, '
    '"choices": [{"label": "HOT", "delta": 0}, {"label": "ICE", "delta": 0}]}, '
    '{"name": "샷 추가", "choices": [{"label": "+1샷", "delta": 500}]}]'
)


def parse_option_groups(raw: str) -> List[OptionGroup]:
    """옵션 JSON 문자열 검증. 잘못되면 ValueError(사용자에게 보여줄 메시지)."""
    raw = (raw or "").strip() or "[]"
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError
        return [OptionGroup.model_validate(g) for g in data]
    except (json.JSONDecodeError, ValidationError, ValueError):
        raise ValueError(f"옵션 JSON 형식이 잘못됐습니다. 예: {OPTIONS_EXAMPLE}")


def item_label(menu_name: str, selected: list[dict]) -> str:
    """'아메리카노 (HOT, +1샷)'"""
    lab = ", ".join(s["choice"] for s in selected)
    return f"{menu_name} ({lab})" if lab else menu_name
