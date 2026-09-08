"""Run the supplied Memory V2 long-conversation dataset through production Memory.

This is an evaluation utility, not a production path.  It keeps one real V2
pipeline alive while assigning every case an independent user/relationship/
conversation scope, so turns within a case can see history without crossing
case boundaries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loveapp.application.memory_inspector import MemoryInspector
from loveapp.bootstrap import build_memory_container
from loveapp.core.config import get_settings
from loveapp.domain.memory import MemoryStatus

CASES: list[dict[str, Any]] = [
    {
        "case_id": "LC-001",
        "difficulty": "easy",
        "title": "用户兴趣长期积累",
        "expected_checks": [
            "food_cuisine=川菜 and taste=麻辣",
            "火锅 is complementary and does not overwrite 川菜",
            "dining convenience preference is added",
            "weekend distance exception does not create contradiction",
        ],
        "turns": [
            "最近我发现自己越来越喜欢吃川菜，尤其喜欢那种比较麻辣的口味。以前其实没有特别偏好，但是这几年经常和朋友出去吃饭，发现这种口味比较符合我的习惯。",
            "对了，我平时也挺喜欢吃火锅，尤其是朋友聚会的时候，经常会主动提议去吃火锅。",
            "最近工作比较忙，所以平时不会专门去找特别远的餐厅，更倾向于公司附近方便一点的地方。",
            "不过如果是周末和朋友聚餐，距离远一点其实也可以接受。",
        ],
    },
    {
        "case_id": "LC-002",
        "difficulty": "easy",
        "title": "简单计划生命周期",
        "expected_checks": [
            "planned event with proposal state",
            "destination update to 黄山",
            "tentative date retained",
            "10月12号 refinement",
        ],
        "turns": [
            "最近一直想着出去旅行，但是还没有完全确定去哪。本来考虑去周边城市走走，主要想找一个节奏慢一点、不太累的地方。",
            "我和朋友昨天讨论了一下，感觉黄山比较合适，风景不错，而且交通也方便。",
            "时间暂时定在国庆之后的第一个周末，不过具体哪一天还没完全确认。",
            "后来我们决定10月12号出发。",
        ],
    },
    {
        "case_id": "LC-003",
        "difficulty": "medium",
        "title": "关系状态长期变化",
        "expected_checks": [
            "interaction pattern",
            "slow reply is a temporary observation",
            "long interaction is an event",
            "belief is separated from fact",
            "主动约饭 supports pattern",
        ],
        "turns": [
            "我和小林最近关系挺好的，基本每天都会聊天，有时候晚上也会聊很久。",
            "最近这两个星期她工作压力比较大，所以回复消息明显慢了一些，有时候几个小时才回。",
            "昨天她下班以后主动找我聊天，我们聊了三个小时。",
            "我感觉她最近可能不是不在乎我，只是工作真的比较忙。",
            "今天她又主动约我周末吃饭。",
        ],
    },
    {
        "case_id": "LC-004",
        "difficulty": "medium",
        "title": "计划取消与恢复",
        "expected_checks": [
            "confirmed Qingchengshan plan",
            "possible cancellation is uncertain and does not delete plan",
            "disappointment does not alter plan state",
            "reconsidered/tentative plan",
            "hypothetical short trip does not create a premature event",
        ],
        "turns": [
            "我和小林之前计划好了国庆后去青城山玩，当时已经讨论过路线，也基本确定会去。",
            "但是昨天她突然说最近工作压力太大，可能没有精力去了。",
            "我当时还有点失落，因为感觉这个计划可能就泡汤了。",
            "后来她今天又和我说，其实还是想去，只是最近确实比较累，到时候看身体状态。",
            "如果最后真的去不了，我们可能会改成附近短途游。",
        ],
    },
    {
        "case_id": "LC-005",
        "difficulty": "medium",
        "title": "事实变化",
        "expected_checks": [
            "Shanghai current work/location fact",
            "possible Hangzhou move does not immediately overwrite",
            "confirmed Hangzhou supersedes Shanghai",
            "possible return to Shanghai remains historical possibility",
        ],
        "turns": [
            "小林现在是在上海工作的，平时也主要住在上海。",
            "最近她公司调整安排，她可能要搬去杭州工作一段时间。",
            "今天已经确定了，她下个月开始会长期在杭州办公。",
            "不过以后如果项目结束，也可能会回上海。",
        ],
    },
    {
        "case_id": "LC-006",
        "difficulty": "hard",
        "title": "用户信念与事实分离",
        "expected_checks": [
            "user belief does not become partner fact",
            "busy-project context is retained",
            "travel planning is evidence/event",
            "belief uncertainty update",
        ],
        "turns": [
            "最近我有点怀疑小林是不是不想继续和我发展下去了，因为她回复消息比以前少很多。",
            "但其实她最近项目特别忙，经常晚上十点以后才下班。",
            "昨天她还主动和我讨论未来一起旅行的计划。",
            "所以我现在也不确定之前的想法是不是正确。",
        ],
    },
    {
        "case_id": "LC-007",
        "difficulty": "hard",
        "title": "Multi Target Ambiguity",
        "expected_checks": [
            "travel proposal with user, Xiaolin, and parents",
            "parents participation only is affected",
            "Xiaolin preference only is affected",
            "participants update without duplicate travel events",
        ],
        "turns": [
            "我和小林还有我爸妈春节的时候可能一起去旅游，目前只是一个想法，还没有正式决定。",
            "我爸妈最近觉得长途旅行太累，所以可能不会参加。",
            "小林倒是觉得出去走走挺好的。",
            "最后可能只有我和小林两个人去了。",
        ],
    },
    {
        "case_id": "LC-008",
        "difficulty": "hard",
        "title": "Long-tail Behavior Pattern",
        "expected_checks": [
            "old low-sharing pattern is retained",
            "new observations do not immediately delete old pattern",
            "new sharing event is evidence",
            "one-month increase may update the pattern",
        ],
        "turns": [
            "小林以前不太喜欢主动分享自己的事情，很多时候都是我问她才会说。",
            "最近她开始主动告诉我工作上的一些事情。",
            "昨天她又主动和我分享了公司发生的一件事情。",
            "最近一个月感觉她主动分享明显比以前多。",
        ],
    },
]


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run only this case id; repeatable.",
    )
    parser.add_argument("--output", type=Path, help="JSON output path.")
    parser.add_argument("--markdown", type=Path, help="Markdown output path.")
    return parser


def _short_memory(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "status",
        "kind",
        "summary",
        "raw_predicate",
        "canonical_predicate",
        "custom_predicate",
        "predicate_family",
        "state_dimension",
        "state_value",
        "perspective",
        "importance",
        "confidence",
        "supersedes_id",
        "source_message_id",
    )
    return {key: item.get(key) for key in keys}


def _case_result(
    case: dict[str, Any],
    reports: list[dict[str, Any]],
    final_all: list[dict[str, Any]],
) -> dict[str, Any]:
    relations = Counter()
    effects = Counter()
    gate_positive = 0
    claims = 0
    judge_called = 0
    for report in reports:
        gate = report.get("gate") or {}
        summary = report.get("summary") or {}
        if gate.get("should_extract"):
            gate_positive += 1
        claims += int(summary.get("extracted_claim_count") or 0)
        relations.update(summary.get("relation_counts") or {})
        effects.update(summary.get("actual_write_effects") or [])
        judge_called += int(
            bool((report.get("memory_pipeline") or {}).get("semantic_judge_called"))
        )
    active = [
        _short_memory(item)
        for item in final_all
        if item.get("status") in {"proposed", "confirmed"}
    ]
    return {
        "case_id": case["case_id"],
        "difficulty": case["difficulty"],
        "title": case["title"],
        "expected_checks": case["expected_checks"],
        "turn_count": len(reports),
        "gate_positive_turns": gate_positive,
        "gate_negative_turns": len(reports) - gate_positive,
        "extracted_claim_count": claims,
        "relation_counts": dict(relations),
        "actual_write_effects": dict(effects),
        "semantic_judge_called_turns": judge_called,
        "final_active_memory_count": len(active),
        "final_active_memories": active,
        "final_all_memory_count": len(final_all),
        "turns": reports,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _render_markdown(payload: dict[str, Any], json_path: Path) -> str:
    lines = [
        "# Memory V2 Long Conversation Evaluation Dataset v2",
        "",
        f"Generated: {payload['generated_at']}",
        "Mode: live V2 production Memory pipeline",
        "Store: one in-process store with independent scope per case",
        "Route: disabled for this memory-focused run",
        "Semantic Judge: enabled, shadow-only",
        f"Raw JSON: {json_path}",
        "",
        "## Overall",
        "",
        f"- Cases: {payload['totals']['case_count']}",
        f"- Turns: {payload['totals']['turn_count']}",
        f"- Gate positive turns: {payload['totals']['gate_positive_turns']}",
        f"- Extracted claims: {payload['totals']['extracted_claim_count']}",
        f"- Final active memories: {payload['totals']['final_active_memory_count']}",
        f"- Semantic Judge called turns: {payload['totals']['semantic_judge_called_turns']}",
        "",
        "| Case | Level | Turns | Gate true | Claims | Active final | Effects |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for case in payload["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['difficulty']} | {case['turn_count']} | "
            f"{case['gate_positive_turns']} | {case['extracted_claim_count']} | "
            f"{case['final_active_memory_count']} | {_json(case['actual_write_effects'])} |"
        )
    for case in payload["cases"]:
        lines.extend(["", f"## {case['case_id']} {case['title']}", "", "Expected checks:"])
        lines.extend(f"- {item}" for item in case["expected_checks"])
        lines.extend(["", "Final active Memory:"])
        if case["final_active_memories"]:
            lines.extend(f"- {_json(item)}" for item in case["final_active_memories"])
        else:
            lines.append("- none")
        for turn in case["turns"]:
            gate = turn.get("gate") or {}
            summary = turn.get("summary") or {}
            lines.extend(
                [
                    "",
                    f"### Turn {turn.get('turn')}: {turn.get('input')}",
                    "",
                    f"Gate: should_extract={gate.get('should_extract')}; "
                    f"reason={gate.get('reason')}; "
                    f"matched_rule={gate.get('matched_rule')}; "
                    f"signals={_json(gate.get('signals'))}",
                    f"Contextual update: {_json(turn.get('contextual_update'))}",
                    f"Model outputs: {_json(turn.get('model_outputs'))}",
                    f"Extracted/normalized/saved: {summary.get('extracted_claim_count', 0)}/"
                    f"{summary.get('normalized_candidate_count', 0)}/"
                    f"{summary.get('saved_memory_count', 0)}",
                    f"Relations: {_json(summary.get('relation_counts'))}",
                    f"Operations: {_json(turn.get('operations'))}",
                    f"Actual diff: {_json(turn.get('diff'))}",
                    f"Audits: {_json(turn.get('audits'))}",
                    "Normalized candidates:",
                ]
            )
            candidates = turn.get("candidates") or []
            if candidates:
                lines.extend(f"- {_json(item)}" for item in candidates)
            else:
                lines.append("- none")
            lines.append("Retrieved/Judge/Validator trace:")
            relations = turn.get("long_tail_relations") or []
            if relations:
                lines.extend(f"- {_json(item)}" for item in relations)
            else:
                lines.append("- none")
            lines.append("DB after turn:")
            after = turn.get("after") or []
            if after:
                lines.extend(f"- {_json(_short_memory(item))}" for item in after)
            else:
                lines.append("- none")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "The supplied qualitative expectations were preserved verbatim.",
            "No force-gate was used; no-durable-signal and zero-claim results are "
            "observed pipeline outcomes.",
            "Semantic Judge remains shadow-only and does not authorize destructive "
            "lifecycle writes.",
        ]
    )
    return "\n".join(lines) + "\n"


async def _run(selected: list[dict[str, Any]]) -> dict[str, Any]:
    settings = get_settings().model_copy(
        update={"memory_backend": "memory", "memory_semantic_relation_provider": "llm"}
    )
    container = build_memory_container(settings)
    results: list[dict[str, Any]] = []
    try:
        for case in selected:
            scope = case["case_id"].lower()
            inspector = MemoryInspector(
                container.memory_service,
                container.memory_store,
                memory_version="v2",
                user_id=f"memory-v2-lc-{scope}-user",
                relationship_id=f"memory-v2-lc-{scope}-relationship",
                conversation_id=f"memory-v2-lc-{scope}-conversation",
                requested_status=MemoryStatus.CONFIRMED,
                limit=200,
            )
            reports: list[dict[str, Any]] = []
            for text in case["turns"]:
                reports.append(await inspector.execute_turn(text))
            final_all = await inspector.list_memories(include_all=True)
            result = _case_result(case, reports, final_all)
            results.append(result)
            print(
                f"{case['case_id']} completed: turns={result['turn_count']} "
                f"claims={result['extracted_claim_count']} "
                f"final_active={result['final_active_memory_count']}",
                flush=True,
            )
    finally:
        await container.aclose()
    return {
        "dataset": "Memory V2 Long Conversation Evaluation Dataset v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "live_v2",
        "route_enabled": False,
        "store_mode": "independent scopes in one in-process Store",
        "memory_pipeline": {
            "memory_version": "v2",
            "semantic_judge_enabled": True,
            "semantic_judge_mode": "shadow",
            "deterministic_relation_enabled": True,
            "destructive_shadow_mutation": False,
        },
        "totals": {
            "case_count": len(results),
            "turn_count": sum(item["turn_count"] for item in results),
            "gate_positive_turns": sum(item["gate_positive_turns"] for item in results),
            "gate_negative_turns": sum(item["gate_negative_turns"] for item in results),
            "extracted_claim_count": sum(item["extracted_claim_count"] for item in results),
            "final_active_memory_count": sum(item["final_active_memory_count"] for item in results),
            "semantic_judge_called_turns": sum(
                item["semantic_judge_called_turns"] for item in results
            ),
        },
        "cases": results,
    }


def main() -> int:
    args = _arg_parser().parse_args()
    selected = CASES
    if args.case_ids:
        wanted = {item.casefold() for item in args.case_ids}
        selected = [item for item in CASES if item["case_id"].casefold() in wanted]
        missing = wanted - {item["case_id"].casefold() for item in selected}
        if missing:
            raise SystemExit(f"Unknown case id(s): {', '.join(sorted(missing))}")
    payload = asyncio.run(_run(selected))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = args.output or Path(
        f".data/evals/memory_v2_long_conversation_eval_v2_{stamp}.json"
    )
    markdown_path = args.markdown or Path(
        f".data/evals/memory_v2_long_conversation_eval_v2_{stamp}.md"
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(payload, json_path), encoding="utf-8")
    print(
        json.dumps(
            {"json": str(json_path), "markdown": str(markdown_path), "totals": payload["totals"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
