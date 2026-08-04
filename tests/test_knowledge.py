from loveapp.adapters.knowledge import InMemoryKnowledgeRetriever
from loveapp.bootstrap import load_seed_documents
from loveapp.domain.enums import AdviceScenario
from loveapp.domain.knowledge import KnowledgeFilters


async def test_seed_documents_are_valid_and_retrievable() -> None:
    documents = load_seed_documents()
    retriever = InMemoryKnowledgeRetriever(documents)

    matches = await retriever.search(
        "和对象吵架以后怎么开口道歉",
        KnowledgeFilters(scenario=AdviceScenario.CONFLICT),
    )

    assert len(documents) >= 6
    assert matches
    assert matches[0].document.id == "conflict_001"
