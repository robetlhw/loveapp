from loveapp.adapters.memory import InMemoryMemoryStore
from loveapp.application import MemoryService
from loveapp.application.memory_gate import MemoryGate
from loveapp.domain.enums import TaskType
from loveapp.domain.memory import AtomicExtraction


class RecordingExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, text: str, **kwargs) -> AtomicExtraction:
        del text, kwargs
        self.calls += 1
        return AtomicExtraction()


async def test_active_date_task_constraint_does_not_reach_durable_extraction() -> None:
    extractor = RecordingExtractor()
    service = MemoryService(InMemoryMemoryStore(), extractor)

    result = await service.remember_text(
        user_id="date-boundary-user",
        relationship_id="primary",
        conversation_id="date-boundary-conversation",
        text="这次预算1000元，去静安区看电影。",
        active_task=TaskType.DATE_PLANNING,
    )

    assert result.saved == []
    assert extractor.calls == 0
    assert result.gate_decision is not None
    assert result.gate_decision.should_extract is False
    assert result.gate_decision.matched_rule == "date_task_local_constraint"


def test_habitual_date_preference_is_not_treated_as_task_local() -> None:
    decision = MemoryGate().evaluate(
        "以后约会我一般预算都在1000元左右。",
        active_task=TaskType.DATE_PLANNING,
    )

    assert decision.should_extract is True
    assert "preference" in decision.signals


def test_long_term_partner_preference_is_not_treated_as_task_local() -> None:
    decision = MemoryGate().evaluate(
        "她一直都不喜欢火锅。",
        active_task=TaskType.DATE_PLANNING,
    )

    assert decision.should_extract is True
    assert "preference" in decision.signals
