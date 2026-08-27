import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DateClause:
    text: str
    start: int
    end: int


def split_date_clauses(text: str) -> list[DateClause]:
    """Split a date request without letting a later clause bind an earlier stop."""

    clauses: list[DateClause] = []
    start = 0
    for match in _CLAUSE_BOUNDARY.finditer(text):
        _append_clause(clauses, text, start, match.start())
        start = match.end()
    _append_clause(clauses, text, start, len(text))
    return clauses


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
        )
    )


_CLAUSE_BOUNDARY = re.compile(
    r"[，,。；;！？!?]+|"
    r"(?:另外|然后|接着)(?=\s*[^，,。；;！？!?])|"
    r"再(?!次)(?=\s*(?:把|将|去|安排|新增|加|看|吃|换|改))"
)
