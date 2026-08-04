import asyncio
from pathlib import Path
from threading import Lock
from typing import Any


class SentenceTransformerEmbeddingProvider:
    def __init__(
        self,
        model_name: str,
        source: str = "modelscope",
        device: str = "cpu",
        batch_size: int = 16,
        cache_path: Path | None = None,
        query_prefix: str = "为这个句子生成表示以用于检索相关文章：",
    ) -> None:
        self.model_name = model_name
        self._source = source
        self._device = device
        self._batch_size = batch_size
        self._cache_path = cache_path
        self._query_prefix = query_prefix
        self._model: Any | None = None
        self._resolved_model_path: str | None = None
        self._model_lock = Lock()
        self._warmup_task: asyncio.Task[None] | None = None
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def start_warmup(self) -> asyncio.Task[None]:
        if self._warmup_task is not None and not self._warmup_task.done():
            return self._warmup_task
        if self._ready:
            self._warmup_task = asyncio.create_task(_completed(), name="embedding-ready")
            return self._warmup_task
        self._warmup_task = asyncio.create_task(
            asyncio.to_thread(self._warmup_sync),
            name="embedding-warmup",
        )
        self._warmup_task.add_done_callback(self._finish_warmup)
        return self._warmup_task

    async def warmup(self) -> None:
        if self._ready:
            return
        task = self.start_warmup()
        try:
            await asyncio.shield(task)
        except BaseException:
            if self._warmup_task is task and task.done():
                self._warmup_task = None
            raise

    async def dimension(self) -> int:
        await self.warmup()
        return await asyncio.to_thread(self._dimension)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        await self.warmup()
        return await asyncio.to_thread(self._encode, texts)

    async def embed_query(self, text: str) -> list[float]:
        await self.warmup()
        vectors = await asyncio.to_thread(self._encode, [f"{self._query_prefix}{text}"])
        return vectors[0]

    async def aclose(self) -> None:
        task = self._warmup_task
        if task is not None and not task.done():
            await asyncio.gather(asyncio.shield(task), return_exceptions=True)

    def _warmup_sync(self) -> None:
        self._encode([f"{self._query_prefix}关系沟通"])

    def _finish_warmup(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is None:
            self._ready = True

    def _dimension(self) -> int:
        model = self._get_model()
        get_dimension = getattr(
            model,
            "get_embedding_dimension",
            model.get_sentence_embedding_dimension,
        )
        dimension = get_dimension()
        if dimension is None:
            return len(self._encode(["dimension probe"])[0])
        return int(dimension)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._get_model().encode(
            texts,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                model_source = self._resolve_model_source()
                cache_folder = (
                    str(self._cache_path)
                    if self._source == "huggingface" and self._cache_path
                    else None
                )
                self._model = SentenceTransformer(
                    model_source,
                    device=self._device,
                    cache_folder=cache_folder,
                )
        return self._model

    def _resolve_model_source(self) -> str:
        if self._resolved_model_path:
            return self._resolved_model_path
        if self._source == "huggingface":
            return self.model_name

        cached_snapshot = self._modelscope_snapshot_path()
        if (cached_snapshot / "config.json").exists() and (
            (cached_snapshot / "model.safetensors").exists()
            or (cached_snapshot / "pytorch_model.bin").exists()
        ):
            self._resolved_model_path = str(cached_snapshot)
            return self._resolved_model_path

        from modelscope import snapshot_download

        cache_dir = str(self._cache_path) if self._cache_path else None
        self._resolved_model_path = snapshot_download(
            self.model_name,
            cache_dir=cache_dir,
        )
        return self._resolved_model_path

    def _modelscope_snapshot_path(self) -> Path:
        cache_root = self._cache_path or Path.home() / ".cache" / "modelscope" / "hub"
        model_directory = self.model_name.replace("/", "--")
        return cache_root / "models" / model_directory / "snapshots" / "master"


async def _completed() -> None:
    return None
