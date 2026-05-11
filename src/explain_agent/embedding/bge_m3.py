from functools import lru_cache
from sentence_transformers import SentenceTransformer


class BGEM3Embedder:
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self._model_name = model_name
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=16,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]


@lru_cache
def get_embedder() -> BGEM3Embedder:
    return BGEM3Embedder()
