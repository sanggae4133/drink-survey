"""pydantic 스키마 — 메뉴 옵션 JSON(저장 형식)의 형태 검증이 핵심. 입력은 cafe_detail.html의 UI."""
import json
from typing import List

from pydantic import BaseModel, Field, ValidationError


class OptionChoice(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    delta_price: int = Field(0, ge=-1_000_000, le=1_000_000)  # 가격 증감(원)


class OptionGroup(BaseModel):
    name: str = Field(min_length=1, max_length=40)   # 예: "온도"
    required: bool = False                            # true면 응답 시 반드시 선택
    choices: List[OptionChoice] = Field(min_length=1, max_length=20)


def parse_option_groups(raw: str) -> List[OptionGroup]:
    """옵션 JSON 문자열 검증. 잘못되면 ValueError(사용자에게 보여줄 메시지)."""
    raw = (raw or "").strip() or "[]"
    try:
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) > 20:
            raise ValueError
        return [OptionGroup.model_validate(g) for g in data]
    except (json.JSONDecodeError, ValidationError, ValueError):
        raise ValueError("옵션 입력이 잘못됐습니다. 그룹마다 선택지가 1개 이상 있어야 하고, "
                         "그룹·선택지는 각 20개, 이름은 40자 이내입니다")


def item_label(menu_name: str, selected: list[dict]) -> str:
    """'아메리카노 (HOT, +1샷)'"""
    lab = ", ".join(s["choice"] for s in selected)
    return f"{menu_name} ({lab})" if lab else menu_name
