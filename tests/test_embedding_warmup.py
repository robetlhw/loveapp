import asyncio
from threading import Event

from loveapp.adapters.embeddings import SentenceTransformerEmbeddingProvider


async def test_first_query_awaits_shared_background_warmup(monkeypatch) -> None:
    provider = SentenceTransformerEmbeddingProvider("fake-model")
    warmup_started = Event()
    warmup_release = Event()
    warmup_calls = 0
    encoded_texts: list[list[str]] = []

    def warmup_sync() -> None:
        nonlocal warmup_calls
        warmup_calls += 1
        warmup_started.set()
        warmup_release.wait(timeout=2)

    def encode(texts: list[str]) -> list[list[float]]:
        encoded_texts.append(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(provider, "_warmup_sync", warmup_sync)
    monkeypatch.setattr(provider, "_encode", encode)

    warmup_task = provider.start_warmup()
    assert provider.start_warmup() is warmup_task
    await asyncio.to_thread(warmup_started.wait, 1)
    query_task = asyncio.create_task(provider.embed_query("测试问题"))
    await asyncio.sleep(0)

    assert not query_task.done()
    warmup_release.set()
    assert await query_task == [1.0, 0.0]
    assert warmup_calls == 1
    assert encoded_texts == [["为这个句子生成表示以用于检索相关文章：测试问题"]]
    await provider.aclose()
