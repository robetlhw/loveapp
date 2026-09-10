"""Run the bounded TwoStage extraction smoke diagnostic.

This harness is intentionally shadow-only: it calls the configured extractor,
never creates a MemoryService/Store, and reports whether the native two-stage
path completed or silently fell back to SingleStage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loveapp.bootstrap import _build_memory_extractor
from loveapp.core.config import get_settings
from loveapp.domain.memory import StoredMessage

_PRE_FIX_BASELINE = {
    "stage1_success_count": 0,
    "stage2_success_count": 0,
    "native_two_stage_count": 0,
    "fallback_count": 18,
    "fallback_reasons": {"STAGE1_SCHEMA_ERROR": 18},
}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _history(case: dict[str, Any], case_index: int) -> list[StoredMessage]:
    base = datetime(2026, 9, 10, tzinfo=UTC) - timedelta(days=case_index)
    return [
        StoredMessage(
            id=f"{case['case_id']}-history-{index}",
            conversation_id=f"two-stage-smoke-{case['case_id']}",
            user_id="two-stage-smoke-user",
            relationship_id="two-stage-smoke-relationship",
            role=item["role"],
            content=item["content"],
            created_at=base + timedelta(minutes=index),
        )
        for index, item in enumerate(case.get("conversation_history", []))
    ]


def _attempts_summary(attempts: list[Any]) -> dict[str, Any]:
    rows = [attempt.model_dump(mode="json") for attempt in attempts]
    coarse = next((row for row in rows if row.get("stage") == "coarse"), None)
    detailed = next((row for row in rows if row.get("stage") == "detailed"), None)
    fallback = next((row for row in rows if row.get("fallback_used")), None)
    failures = [row for row in rows if row.get("status") == "failed"]
    stage2_called = detailed is not None
    return {
        "attempts": rows,
        "stage1_success": bool(coarse and coarse.get("status") == "completed"),
        "stage2_called": stage2_called,
        "stage2_success": (
            bool(detailed and detailed.get("status") == "completed") if stage2_called else None
        ),
        "fallback_triggered": fallback is not None,
        "fallback_stage": (failures[0].get("stage") if failures else None),
        "fallback_reason": (failures[0].get("failure_category") if failures else None),
        "fallback_error": (failures[0].get("error") if failures else None),
        "final_extractor_used": "single_stage_fallback" if fallback else "two_stage_native",
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args.dataset)
    settings = get_settings().model_copy(update={"memory_extraction_mode": "two_stage"})
    extractor = _build_memory_extractor(settings)
    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases):
            attempts: list[Any] = []
            extraction = await extractor.extract(
                case["text"],
                reference_time=datetime(2026, 9, 10, tzinfo=UTC),
                existing_memories=[],
                conversation_history=_history(case, index),
                pending_memory_context=None,
                attempt_callback=attempts.append,
            )
            trace = _attempts_summary(attempts)
            diagnostic = getattr(extractor, "last_diagnostic", {})
            fallback_diag = diagnostic.get("fallback", {})
            if fallback_diag.get("reason_code"):
                trace["fallback_reason"] = fallback_diag["reason_code"]
            if fallback_diag.get("stage"):
                trace["fallback_stage"] = fallback_diag["stage"]
            if fallback_diag.get("exception_message"):
                trace["fallback_error"] = fallback_diag["exception_message"]
            stage2_display = (
                "N/A"
                if not trace["stage2_called"]
                else ("PASS" if trace["stage2_success"] else "FAIL")
            )
            expected = case.get("expected", {})
            stage1_props = diagnostic.get("stage1", {}).get("propositions", [])
            candidate_kinds = {
                kind
                for proposition in stage1_props
                for kind in proposition.get("candidate_kinds", [])
            }
            expected_kinds = set(expected.get("kinds", []))
            claim_count = len(extraction.claims)
            min_props = int(expected.get("min_propositions", 0))
            expected_claim_count = int(expected.get("claims", 0))
            rows.append(
                {
                    "case_id": case["case_id"],
                    "title": case.get("title"),
                    "text": case["text"],
                    "recent_conversation": case.get("conversation_history", []),
                    "pending_memory_context": None,
                    "existing_memories": case.get("existing_memories", []),
                    "expected": expected,
                    "stage1_success": trace["stage1_success"],
                    "stage2_called": trace["stage2_called"],
                    "stage2_success": trace["stage2_success"],
                    "native_two_stage": not trace["fallback_triggered"],
                    "fallback": {
                        key: trace[key]
                        for key in (
                            "fallback_triggered",
                            "fallback_stage",
                            "fallback_reason",
                            "fallback_error",
                        )
                    },
                    "final_extractor_used": trace["final_extractor_used"],
                    "final_claim_count": claim_count,
                    "stage1_proposition_count": len(stage1_props),
                    "stage1_proposition_count_ok": len(stage1_props) >= min_props,
                    "stage1_kind_coverage": sorted(candidate_kinds & expected_kinds),
                    "stage1_kind_coverage_ok": expected_kinds <= candidate_kinds,
                    "claim_count_ok": claim_count >= expected_claim_count,
                    "final_claims": [claim.model_dump(mode="json") for claim in extraction.claims],
                    "semantic_gate": {
                        "should_extract": extraction.should_extract,
                        "gate_reason": (
                            extraction.gate_reason.value if extraction.gate_reason else None
                        ),
                    },
                    "attempts": trace["attempts"],
                    "diagnostic": diagnostic,
                }
            )
            print(
                f"{case['case_id']}: stage1={'PASS' if trace['stage1_success'] else 'FAIL'} "
                f"stage2={stage2_display} "
                f"native={'PASS' if not trace['fallback_triggered'] else 'FAIL'} "
                f"reason={trace['fallback_reason'] or '-'}",
                flush=True,
            )
    finally:
        await extractor.aclose()

    fallback_reasons = Counter(
        row["fallback"]["fallback_reason"] for row in rows if row["fallback"]["fallback_reason"]
    )
    multi_rows = [row for row in rows if row["expected"].get("min_propositions", 0) > 1]
    summary = {
        "evaluation": "two_stage_extraction_diagnostic_v0_1",
        "dataset": str(args.dataset),
        "case_count": len(rows),
        "stage1_success_count": sum(row["stage1_success"] for row in rows),
        "stage2_expected_count": sum(row["stage2_called"] for row in rows),
        "stage2_success_count": sum(row["stage2_success"] is True for row in rows),
        "native_two_stage_count": sum(row["native_two_stage"] for row in rows),
        "fallback_count": sum(not row["native_two_stage"] for row in rows),
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        "multi_proposition_case_count": len(multi_rows),
        "multi_claim_case_count": sum(
            row["final_claim_count"] >= row["expected"].get("claims", 0) > 1 for row in multi_rows
        ),
        "multi_proposition_decomposition_count": sum(
            row["stage1_proposition_count_ok"] for row in multi_rows
        ),
        "multi_claim_preservation_count": sum(
            row["claim_count_ok"] for row in multi_rows
        ),
        "store_mutation_permitted": False,
        "pre_fix_baseline": _PRE_FIX_BASELINE,
        "cost": _cost_summary(rows),
        "cases": rows,
    }
    return summary


def _cost_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [attempt for row in rows for attempt in row["attempts"]]
    total_tokens = sum(int(attempt.get("total_tokens") or 0) for attempt in attempts)
    return {
        "call_count": len(attempts),
        "prompt_tokens": sum(int(attempt.get("prompt_tokens") or 0) for attempt in attempts),
        "completion_tokens": sum(
            int(attempt.get("completion_tokens") or 0) for attempt in attempts
        ),
        "total_tokens": total_tokens,
        "avg_calls_per_case": round(len(attempts) / len(rows), 4) if rows else None,
        "avg_tokens_per_case": round(total_tokens / len(rows), 4) if rows else None,
    }


def _render_summary(report: dict[str, Any]) -> str:
    before = report["pre_fix_baseline"]
    lines = [
        "# TwoStage Extraction Diagnostic v0.1",
        "",
        "## Before fix",
        "",
        f"- Stage1 success: `{before['stage1_success_count']}/{report['case_count']}`",
        f"- Stage2 success: `{before['stage2_success_count']}`",
        f"- Native TwoStage completion: "
        f"`{before['native_two_stage_count']}/{report['case_count']}`",
        f"- Fallback: `{before['fallback_count']}/{report['case_count']}`",
        f"- Fallback reasons: `{before['fallback_reasons']}`",
        "",
        "Primary cause: Stage1 prompt-output contract mismatch produced invalid "
        "CoarseExtraction fields and enums.",
        "",
        "## After fix",
        "",
        f"Cases: `{report['case_count']}`",
        f"Stage1 success: `{report['stage1_success_count']}/{report['case_count']}`",
        f"Stage2 success: `{report['stage2_success_count']}/"
        f"{report['stage2_expected_count']}` (not called for "
        f"`{report['case_count'] - report['stage2_expected_count']}` negative-gate cases)",
        f"Native TwoStage completion: `{report['native_two_stage_count']}/{report['case_count']}`",
        f"Fallback count: `{report['fallback_count']}`",
        f"Multi-claim cases meeting expected count: `{report['multi_claim_case_count']}/"
        f"{report['multi_proposition_case_count']}`",
        f"Stage1 multi-proposition decomposition: `"
        f"{report['multi_proposition_decomposition_count']}/"
        f"{report['multi_proposition_case_count']}`",
        f"Stage2 multi-claim preservation: `"
        f"{report['multi_claim_preservation_count']}/"
        f"{report['multi_proposition_case_count']}`",
        "",
        "## Fallback reasons",
        "",
    ]
    if report["fallback_reasons"]:
        lines.extend(f"- `{key}`: `{value}`" for key, value in report["fallback_reasons"].items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Multi-claim cases",
            "",
            "| Case | Stage1 props | Expected min | Stage2 claims | Expected | "
            "Kind coverage |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["cases"]:
        if row["expected"].get("min_propositions", 0) <= 1:
            continue
        lines.append(
            f"| {row['case_id']} | {row['stage1_proposition_count']} | "
            f"{row['expected']['min_propositions']} | {row['final_claim_count']} | "
            f"{row['expected']['claims']} | "
            f"{'PASS' if row['stage1_kind_coverage_ok'] else 'FAIL'} |"
        )
    cost = report["cost"]
    lines.extend(
        [
            "",
            "## Cost",
            "",
            f"- Calls: `{cost['call_count']}` "
            f"(`{cost['avg_calls_per_case']}` per case)",
            f"- Tokens: `{cost['total_tokens']}` "
            f"(`{cost['avg_tokens_per_case']}` per case)",
            f"- Prompt/completion tokens: "
            f"`{cost['prompt_tokens']}/{cost['completion_tokens']}`",
            "",
            "## Cases",
            "",
            "| Case | Stage1 | Stage2 | Native | Reason |",
            "|---|---|---|---|---|",
        ]
    )
    for row in report["cases"]:
        stage2_display = (
            "N/A" if not row["stage2_called"] else ("PASS" if row["stage2_success"] else "FAIL")
        )
        lines.append(
            f"| {row['case_id']} | {'PASS' if row['stage1_success'] else 'FAIL'} | "
            f"{stage2_display} | "
            f"{'PASS' if row['native_two_stage'] else 'FAIL'} | "
            f"{row['fallback']['fallback_reason'] or '-'} |"
        )
    lines.extend(["", "Store mutation permitted: `False`", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/memory/two_stage_extraction_smoke_v0_1.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/two_stage_diagnostic_v0_1"),
    )
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / "raw_results.jsonl"
    raw_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in report["cases"]),
        encoding="utf-8",
    )
    (args.output_dir / "summary.md").write_text(_render_summary(report), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "case_count",
                    "stage1_success_count",
                    "stage2_success_count",
                    "native_two_stage_count",
                    "fallback_count",
                    "fallback_reasons",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
