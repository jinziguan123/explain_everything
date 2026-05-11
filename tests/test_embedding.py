import pytest
from explain_agent.embedding.bge_m3 import BGEM3Embedder


@pytest.mark.integration
@pytest.mark.slow
def test_embed_returns_1024_dim_vectors():
    embedder = BGEM3Embedder()
    texts = ["半导体板块今日大涨", "美联储宣布加息"]
    vectors = embedder.embed(texts)
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
    import numpy as np
    v1, v2 = np.array(vectors[0]), np.array(vectors[1])
    cos = (v1 @ v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
    assert 0 <= cos <= 1
