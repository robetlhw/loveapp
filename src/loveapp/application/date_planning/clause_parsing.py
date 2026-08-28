import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DateClause:
    text: str
    start: int
    end: int
    source_text: str


def split_date_clauses(text: str) -> list[DateClause]:
    """Split a date request without letting a later clause bind an earlier stop."""

    clauses: list[DateClause] = []
    start = 0
    for match in _CLAUSE_BOUNDARY.finditer(text):
        _append_clause(clauses, text, start, match.start())
        start = match.end()
    _append_clause(clauses, text, start, len(text))
    return _fold_temporal_prefixes(clauses, text)


def _fold_temporal_prefixes(
    clauses: list[DateClause],
    source_text: str,
) -> list[DateClause]:
    folded: list[DateClause] = []
    index = 0
    while index < len(clauses):
        clause = clauses[index]
        if _TEMPORAL_PREFIX_ONLY.fullmatch(clause.text) is None or index + 1 >= len(clauses):
            folded.append(clause)
            index += 1
            continue
        prefixes = [clause]
        index += 1
        while (
            index < len(clauses) - 1
            and _TEMPORAL_PREFIX_ONLY.fullmatch(clauses[index].text) is not None
        ):
            prefixes.append(clauses[index])
            index += 1
        action = clauses[index]
        folded.append(
            DateClause(
                text="".join([*(prefix.text for prefix in prefixes), action.text]),
                start=prefixes[0].start,
                end=action.end,
                source_text=source_text[prefixes[0].start : action.end].strip(),
            )
        )
        index += 1
    return folded


def _append_clause(
    clauses: list[DateClause],
    text: str,
    start: int,
    end: int,
) -> None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    leading = len(raw) - len(raw.lstrip())
    clause_start = start + leading
    clauses.append(
        DateClause(
            text=stripped,
            start=clause_start,
            end=clause_start + len(stripped),
            source_text=stripped,
        )
    )


_CLAUSE_BOUNDARY = re.compile(
    r"[，,。；;！？!?]+|"
    r"(?:另外|然后|接着)(?=\s*[^，,。；;！？!?])|"
    r"再(?!次)(?=\s*(?:把|将|去|安排|新增|加|看|吃|换|改))"
)

_TEMPORAL_PREFIX_ONLY = re.compile(
    r"(?:(?:吃完)?(?:早餐|早饭|午餐|午饭|中饭|晚餐|晚饭|晚宴)(?:后|之后|前|之前)|"
    r"(?:电影|活动|演出|用餐)(?:结束|完成)?(?:后|之后|前|之前)|"
    r"上午|中午|下午|傍晚|晚上)"
)
