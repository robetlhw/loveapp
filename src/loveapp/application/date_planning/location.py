import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedDateLocation:
    city: str | None = None
    area: str | None = None


_CITY_NAMES = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "苏州",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "长沙",
    "天津",
    "青岛",
    "厦门",
    "郑州",
    "济南",
    "合肥",
    "福州",
    "昆明",
    "大连",
    "宁波",
    "无锡",
    "佛山",
    "东莞",
    "珠海",
    "三亚",
    "沈阳",
    "哈尔滨",
    "长春",
    "石家庄",
    "南昌",
    "贵阳",
    "南宁",
    "太原",
    "兰州",
    "乌鲁木齐",
    "呼和浩特",
    "海口",
    "泉州",
    "温州",
    "绍兴",
    "嘉兴",
)
_CITY_ALIASES = {
    alias: city
    for city in _CITY_NAMES
    for alias in (city, f"{city}市")
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
