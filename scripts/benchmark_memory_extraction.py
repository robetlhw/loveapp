"""Measure the configured Flash memory extractor without touching app data."""

import asyncio
import json
from math import ceil
from statistics import median
from time import perf_counter

from loveapp.bootstrap import build_memory_container
from loveapp.core.config import Settings
from loveapp.core.timing import ExecutionTrace

CASES = (
    "她喜欢粤菜，订餐时可以优先考虑她的口味。",
    "我平时周末喜欢看展览，约会安排可以优先考虑展览。",
    "我喜欢安静的咖啡馆，嘈杂的地方不太适合我。",
)


async def main() -> None:
    settings = Settings().model_copy(update={"memory_backend": "memory"})
    flash_model = settings.memory_extraction_model or settings.llm_model
    strong_model = settings.memory_extraction_strong_model or settings.llm_model
    container = build_memory_container(settings)
    rows: list[dict[str, object]] = []
    try:
        for index, text in enumerate(CASES, start=1):
            trace = ExecutionTrace()
            started = perf_counter()
            result = await container.memory_service.remember_text(
                user_id=f"memory-smoke-user-{index}",
                relationship_id=f"memory-smoke-relationship-{index}",
                conversation_id=f"memory-smoke-conversation-{index}",
                text=text,
                trace=trace,
            )
            duration_ms = (perf_counter() - started) * 1000
            records = trace.snapshot()
            attempts = [
                record
                for record in records
                if record.name == "memory_model_attempt_1"
                or record.name.startswith("memory_model_strong_attempt_")
            ]
            discarded_invalid = any(
                record.name == "memory_extraction_upgrade_gate"
                and record.details.get("discard_reason")
                for record in records
            )
            rows.append(
                {
                    "case": index,
                    "duration_ms": round(duration_ms, 2),
                    "gate": (
                        result.gate_decision.reason.value
                        if result.gate_decision is not None
                        else None
                    ),
                    "saved_count": len(result.saved),
                    "discarded_invalid": discarded_invalid,
                    "attempts": [
                        {
                            "name": record.name,
                            "duration_ms": round(record.duration_ms, 2),
                            "status": record.status.value,
                            "tier": record.details.get("tier"),
                            "repair_status": record.details.get("repair_status"),
                            "claim_count": record.details.get("claim_count"),
                            "claim_confidences": record.details.get("claim_confidences"),
                            "failure_category": record.details.get("failure_category"),
                        }
                        for record in attempts
                    ],
                }
            )
    finally:
        await container.aclose()

    durations = [float(row["duration_ms"]) for row in rows]
    flash_calls = sum(
        any(attempt["name"] == "memory_model_attempt_1" for attempt in row["attempts"])
        for row in rows
    )
    strong_upgrades = sum(
        any(
            str(attempt["name"]).startswith("memory_model_strong_attempt_")
            for attempt in row["attempts"]
        )
        for row in rows
    )
    direct_success = sum(
        any(
            attempt["name"] == "memory_model_attempt_1"
            and attempt["status"] == "completed"
            and attempt["repair_status"] == "direct"
            for attempt in row["attempts"]
        )
        for row in rows
    )
    local_repair = sum(
        any(
            attempt["name"] == "memory_model_attempt_1"
            and attempt["status"] == "completed"
            and attempt["repair_status"] == "local_repair"
            for attempt in row["attempts"]
        )
        for row in rows
    )
    discarded_invalid = sum(bool(row["discarded_invalid"]) for row in rows)
    ordered_durations = sorted(durations)
    p95_index = min(len(ordered_durations) - 1, max(0, ceil(len(durations) * 0.95) - 1))
    print(
        json.dumps(
            {
                "flash_model": flash_model,
                "strong_model": strong_model if strong_model != flash_model else None,
                "strong_model_configured": bool(strong_model and strong_model != flash_model),
                "flash_timeout_seconds": settings.memory_extraction_timeout_seconds,
                "flash_max_retries": settings.memory_extraction_max_retries,
                "flash_max_tokens": settings.memory_extraction_max_tokens,
                "flash_thinking": settings.memory_extraction_thinking,
                "strong_thinking": settings.memory_extraction_strong_thinking,
                "case_count": len(rows),
                "flash_calls": flash_calls,
                "direct_success": direct_success,
                "direct_success_rate": round(direct_success / flash_calls, 4)
                if flash_calls
                else 0,
                "local_repair_success": local_repair,
                "discarded_invalid": discarded_invalid,
                "strong_upgrades": strong_upgrades,
                "strong_upgrade_rate": round(strong_upgrades / flash_calls, 4)
                if flash_calls
                else 0,
                "p50_total_latency_ms": round(median(durations), 2) if durations else 0,
                "p95_total_latency_ms": (
                    round(ordered_durations[p95_index], 2) if durations else 0
                ),
                "max_total_latency_ms": round(max(durations), 2) if durations else 0,
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
