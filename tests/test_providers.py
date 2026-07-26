"""Tests for the embedding / reranking provider abstraction.

These tests verify:
- Local providers return correct dimensions and structure
- Remote providers handle HTTP success, auth errors, and contract mismatch
- Provider switching works via config

Run with:
    pytest tests/test_providers.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import httpx

from src.providers import (
    Embedding,
    EmbedProvider,
    LocalEmbedProvider,
    RemoteEmbedProvider,
    LocalRerankProvider,
    RemoteRerankProvider,
    GpuServiceUnavailable,
    GpuServiceAuthError,
    GpuServiceContractError,
    get_embed_provider,
    get_rerank_provider,
    reset_providers,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, json_data: dict | None = None) -> MagicMock:
    """Build a mock ``httpx.Response`` whose ``raise_for_status`` actually
    raises ``HTTPStatusError`` for non-2xx codes, matching real behaviour."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp,
        )
    return resp


# ── Embedding structure ──────────────────────────────────────────────────────

class TestEmbeddingStructure:
    """Verify Embedding dataclass and basic local provider output."""

    def test_embedding_dataclass(self):
        emb = Embedding(dense=[0.1, 0.2], sparse_indices=[1, 2], sparse_values=[0.5, 0.6])
        assert emb.dense == [0.1, 0.2]
        assert emb.sparse_indices == [1, 2]
        assert emb.sparse_values == [0.5, 0.6]


# ── Local provider (mocked model) ────────────────────────────────────────────

class TestLocalEmbedProvider:
    """Local provider with mocked BGEM3FlagModel."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_flagembedding(self):
        pytest.importorskip("FlagEmbedding", reason="FlagEmbedding not installed")

    @pytest.fixture
    def mock_model(self):
        with patch("FlagEmbedding.BGEM3FlagModel") as MockModel:
            instance = MockModel.return_value
            import numpy as np
            instance.encode.return_value = {
                "dense_vecs": np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]),
                "lexical_weights": [
                    {1: 0.5, 2: 0.6},
                    {3: 0.7},
                ],
            }
            yield instance

    @pytest.fixture
    def provider(self, mock_model):
        p = LocalEmbedProvider()
        p._loaded = True
        p._model = mock_model
        return p

    def test_encode_batch(self, provider):
        results = provider.encode(["hello", "world"])
        assert len(results) == 2
        assert results[0].dense == [0.1, 0.2, 0.3]
        assert results[0].sparse_indices == [1, 2]
        assert results[0].sparse_values == [0.5, 0.6]
        assert results[1].sparse_indices == [3]
        assert results[1].sparse_values == [0.7]

    def test_encode_one(self, provider):
        result = provider.encode_one("hello")
        assert isinstance(result, Embedding)
        assert len(result.dense) == 3

    def test_encode_empty(self, provider):
        results = provider.encode([])
        assert results == []


class TestLocalRerankProvider:
    """Local reranker with mocked FlagReranker."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_flagembedding(self):
        pytest.importorskip("FlagEmbedding", reason="FlagEmbedding not installed")

    @pytest.fixture
    def mock_reranker(self):
        with patch("FlagEmbedding.FlagReranker") as MockReranker:
            instance = MockReranker.return_value
            instance.compute_score.return_value = [0.9, 0.3, 0.7]
            yield instance

    @pytest.fixture
    def provider(self, mock_reranker):
        p = LocalRerankProvider()
        p._loaded = True
        p._reranker = mock_reranker
        return p

    def test_rerank_scores(self, provider):
        scores = provider.rerank_scores("query", ["a", "b", "c"])
        assert len(scores) == 3
        assert scores == [0.9, 0.3, 0.7]

    def test_rerank_empty(self, provider):
        scores = provider.rerank_scores("query", [])
        assert scores == []

    def test_rerank_single_preserves_list(self, provider):
        provider._reranker.compute_score.return_value = [0.8]
        scores = provider.rerank_scores("query", ["only"])
        assert isinstance(scores, list)
        assert len(scores) == 1


# ── Remote provider (mocked HTTP) ────────────────────────────────────────────

MODEL_INFO_OK = {
    "api_version": "1",
    "embedding_model": "BAAI/bge-m3",
    "embedding_dimension": 1024,
    "reranker_model": "BAAI/bge-reranker-v2-m3",
    "device": "cuda",
}

EMBED_RESPONSE_OK = {
    "embeddings": [
        {"dense": [0.1, 0.2, 0.3], "sparse_indices": [1, 2], "sparse_values": [0.5, 0.6]},
        {"dense": [0.4, 0.5, 0.6], "sparse_indices": [3], "sparse_values": [0.7]},
    ],
}

RERANK_RESPONSE_OK = {"scores": [0.9, 0.3]}


class TestRemoteEmbedProvider:
    """Remote provider with mocked httpx client."""

    @pytest.fixture
    def mock_client(self):
        with patch("src.providers.httpx.Client") as MockClient:
            client = MockClient.return_value
            client.get.return_value = _mock_response(200, MODEL_INFO_OK)
            client.post.return_value = _mock_response(200, EMBED_RESPONSE_OK)
            yield client

    @pytest.fixture
    def provider(self, mock_client):
        return RemoteEmbedProvider()

    def test_contract_check(self, provider):
        """Should have called /model-info during init."""
        # provider fixture already constructed RemoteEmbedProvider, which calls
        # _check_contract() in __init__
        from src.providers import GPU_SERVICE_URL
        # The mock client's get was called during __init__
        import src.providers as p
        # Access the private client through the provider
        provider._client.get.assert_called_once_with("/model-info")

    def test_encode_batch(self, provider):
        results = provider.encode(["hello", "world"])
        assert len(results) == 2
        assert results[0].dense == [0.1, 0.2, 0.3]

    def test_encode_one(self, provider):
        result = provider.encode_one("hello")
        assert isinstance(result, Embedding)

    def test_encode_empty(self, provider):
        results = provider.encode([])
        assert results == []


class TestRemoteEmbedProviderErrors:
    """Remote provider error handling."""

    @pytest.fixture
    def mock_client(self):
        with patch("src.providers.httpx.Client") as MockClient:
            client = MockClient.return_value
            yield client

    def test_auth_error(self, mock_client):
        """401 from /model-info raises GpuServiceAuthError."""
        mock_client.get.return_value = _mock_response(401)
        with pytest.raises(GpuServiceAuthError):
            RemoteEmbedProvider()

    def test_contract_dimension_mismatch(self, mock_client):
        """Wrong embedding dimension raises GpuServiceContractError."""
        info = dict(MODEL_INFO_OK)
        info["embedding_dimension"] = 512
        mock_client.get.return_value = _mock_response(200, info)
        with pytest.raises(GpuServiceContractError):
            RemoteEmbedProvider()

    def test_connection_refused(self, mock_client):
        """Connection error raises GpuServiceUnavailable."""
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        with pytest.raises(GpuServiceUnavailable):
            RemoteEmbedProvider()

    def test_timeout(self, mock_client):
        """Timeout raises GpuServiceUnavailable."""
        mock_client.get.side_effect = httpx.TimeoutException("Timed out")
        with pytest.raises(GpuServiceUnavailable):
            RemoteEmbedProvider()

    def test_embed_503(self, mock_client):
        """503 from embed raises GpuServiceUnavailable."""
        mock_client.get.return_value = _mock_response(200, MODEL_INFO_OK)
        mock_client.post.return_value = _mock_response(503)
        provider = RemoteEmbedProvider()
        with pytest.raises(GpuServiceUnavailable):
            provider.encode(["hello"])


class TestRemoteRerankProvider:
    """Remote reranker with mocked HTTP."""

    @pytest.fixture
    def mock_client(self):
        with patch("src.providers.httpx.Client") as MockClient:
            client = MockClient.return_value
            client.post.return_value = _mock_response(200, RERANK_RESPONSE_OK)
            yield client

    @pytest.fixture
    def provider(self, mock_client):
        return RemoteRerankProvider()

    def test_rerank_scores(self, provider):
        scores = provider.rerank_scores("query", ["a", "b"])
        assert scores == [0.9, 0.3]

    def test_rerank_empty(self, provider):
        scores = provider.rerank_scores("query", [])
        assert scores == []


# ── Provider getter / switching ─────────────────────────────────────────────

class TestProviderSwitching:
    """Verify provider selection via config."""

    def teardown_method(self):
        reset_providers()

    @patch("src.providers.LocalEmbedProvider")
    def test_default_is_local_embed(self, MockLocal):
        with patch("src.config.EMBED_PROVIDER", "local"):
            provider = get_embed_provider()
            MockLocal.assert_called_once()

    @patch("src.providers.RemoteEmbedProvider")
    def test_remote_embed(self, MockRemote):
        with patch("src.config.EMBED_PROVIDER", "remote"):
            with patch("src.config.GPU_SERVICE_URL", "http://test:8100"):
                with patch("src.config.GPU_SERVICE_TOKEN", "test-token"):
                    provider = get_embed_provider()
                    MockRemote.assert_called_once()

    @patch("src.providers.LocalRerankProvider")
    def test_default_is_local_rerank(self, MockLocal):
        with patch("src.config.RERANK_PROVIDER", "local"):
            provider = get_rerank_provider()
            MockLocal.assert_called_once()

    @patch("src.providers.RemoteRerankProvider")
    def test_remote_rerank(self, MockRemote):
        with patch("src.config.RERANK_PROVIDER", "remote"):
            with patch("src.config.GPU_SERVICE_URL", "http://test:8100"):
                with patch("src.config.GPU_SERVICE_TOKEN", "test-token"):
                    provider = get_rerank_provider()
                    MockRemote.assert_called_once()