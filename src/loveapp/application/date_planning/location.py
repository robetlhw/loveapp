import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedDateLocation:
    city: str | None = None
    area: str | None = None


_CITY_ALIASES = {
    "上海市": "上海",
    "上海": "上海",
    "杭州市": "杭州",
    "杭州": "杭州",
    "北京市": "北京",
    "北京": "北京",
    "广州市": "广州",
    "广州": "广州",
    "深圳市": "深圳",
    "深圳": "深圳",
}
_AREA_CITY = {
    "静安区": "上海",
    "徐汇区": "上海",
    "黄浦区": "上海",
    "浦东新区": "上海",
    "杨浦区": "上海",
    "南京西路": "上海",
    "陆家嘴": "上海",
    "徐家汇": "上海",
    "五角场": "上海",
    "西湖区": "杭州",
}


def resolve_date_location(
    text: str,
    *,
    city: str | None = None,
    area: str | None = None,
) -> ResolvedDateLocation:
    """Resolve a date-planning city and area without discarding either slot."""

    normalized = re.sub(r"\s+", "", text)
    resolved_city = _normalize_city(city) or _find_city(normalized)
    resolved_area = area or _find_area(normalized, resolved_city)
    if resolved_area is not None and resolved_city is None:
        resolved_city = _AREA_CITY.get(resolved_area)
    return ResolvedDateLocation(city=resolved_city, area=resolved_area)


def _normalize_city(value: str | None) -> str | None:
    if value is None:
        return None
    return _CITY_ALIASES.get(value, value.removesuffix("市"))


def _find_city(text: str) -> str | None:
    for alias in sorted(_CITY_ALIASES, key=len, reverse=True):
        if alias in text:
            return _CITY_ALIASES[alias]
    return None


def _find_area(text: str, city: str | None) -> str | None:
    for candidate in sorted(_AREA_CITY, key=len, reverse=True):
        if candidate in text:
            return candidate
    if city is not None:
        city_pattern = re.escape(city) + r"(?:市)?[，,、]*([\u4e00-\u9fff]{2,8}?(?:区|县|商圈))"
        match = re.search(city_pattern, text)
        if match is not None:
            return match.group(1)
    for pattern in (
        r"(?:区域|商圈|地点)\s*(?:是|为|在|:)?\s*([\u4e00-\u9fff]{2,8}?(?:区|县|商圈))",
        r"(?:想去|去|到|在|前往)\s*([\u4e00-\u9fff]{2,8}?(?:区|县|商圈))",
        r"(?:^|[，,、])([\u4e00-\u9fff]{2,8}?(?:区|县|商圈))",
    ):
        match = re.search(pattern, text)
        if match is not None:
            return match.group(1)
    return None
