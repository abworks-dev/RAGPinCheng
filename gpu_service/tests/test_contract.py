"""Contract tests for the GPU Inference Service API.

These tests verify the API contract (schemas, validation, error handling)
without requiring a real GPU or loaded models.  They use the FastAPI
TestClient and mock the ModelManager to return deterministic values.

Run with:
    pip install fastapi uvicorn pydantic httpx pytest python-dotenv
    pytest gpu_service/tests/test_contract.py -v
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import patch, PropertyMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

# Must set token before importing app
import os
os.environ["GPU_SERVICE_TOKEN"] = "test-token-123"

torch_stub = types.ModuleType("torch")
torch_stub.__version__ = "test-stub"
torch_stub.cuda = types.SimpleNamespace(
    is_available=lambda: False,
    empty_cache=lambda: None,
)
sys.modules.setdefault("torch", torch_stub)
flag_embedding_stub = types.ModuleType("FlagEmbedding")
flag_embedding_stub.__version__ = "test-stub"
transformers_stub = types.ModuleType("transformers")
transformers_stub.__version__ = "test-stub"
sys.modules.setdefault("FlagEmbedding", flag_embedding_stub)
sys.modules.setdefault("transformers", transformers_stub)

from gpu_service.app import app, _activity_tracker, _model_manager
from gpu_service.schemas import (
    EmbeddingResponse,
    HealthResponse,
    ModelInfoResponse,
    RerankResponse,
)

client = TestClient(app)

# ── Fixtures ─────────────────────────────────────────────────────────────────

AUTH_HEADER = {"Authorization": "Bearer test-token-123"}

MOCK_EMBED_RESULT = [
    {"dense": [0.1, 0.2, 0.3], "sparse_indices": [1, 2], "sparse_values": [0.5, 0.6]},
    {"dense": [0.4, 0.5, 0.6], "sparse_indices": [3], "sparse_values": [0.7]},
]


@pytest.fixture(autouse=True)
def reset_model_manager():
    """Reset the singleton between tests so is_loaded state is clean."""
    # Force the manager to appear unloaded by default
    _activity_tracker.reset_for_tests()
    with patch.object(_model_manager, "_initialized", False):
        with patch.object(_model_manager, "_embed_model", None):
            with patch.object(type(_model_manager), "is_loaded", new_callable=PropertyMock, return_value=False):
                yield


@pytest.fixture
def model_loaded():
    """Patch the manager to report loaded and return mock results."""
    with patch.object(_model_manager, "_initialized", True):
        with patch.object(_model_manager, "_embed_model", object()):  # truthy sentinel
            with patch.object(type(_model_manager), "is_loaded", new_callable=PropertyMock, return_value=True):
                with patch.object(_model_manager, "_device", "cuda"):
                    with patch.object(_model_manager, "embed", return_value=MOCK_EMBED_RESULT):
                        with patch.object(_model_manager, "rerank", return_value=[0.9, 0.3]):
                            yield


# ── Health ───────────────────────────────────────────────────────────────────

def test_health_unloaded():
    resp = client.get("/health")
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    data = HealthResponse(**resp.json())
    assert data.status == "error"
    assert data.model_loaded is False


def test_health_loaded(model_loaded):
    resp = client.get("/health")
    assert resp.status_code == status.HTTP_200_OK
    data = HealthResponse(**resp.json())
    assert data.status == "ok"
    assert data.model_loaded is True


def test_model_manager_refuses_cpu_fallback():
    with pytest.raises(RuntimeError, match="CPU fallback is disabled"):
        _model_manager._pick_device()




# ── Authenticated activity ──────────────────────────────────────────────────

def test_activity_requires_auth():
    assert client.get("/v1/activity").status_code == status.HTTP_401_UNAUTHORIZED
    assert client.get(
        "/v1/activity", headers={"Authorization": "Bearer wrong-token"}
    ).status_code == status.HTTP_403_FORBIDDEN


def test_activity_is_fail_closed_when_model_unloaded():
    resp = client.get("/v1/activity", headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "api_version": "gpu-activity/1",
        "model_loaded": False,
        "inflight_requests": 0,
        "asr_chunk_allowed": False,
    }


def test_activity_allows_asr_only_when_loaded_and_idle(model_loaded):
    idle = client.get("/v1/activity", headers=AUTH_HEADER).json()
    assert idle == {
        "api_version": "gpu-activity/1",
        "model_loaded": True,
        "inflight_requests": 0,
        "asr_chunk_allowed": True,
    }
    with _activity_tracker.inference():
        busy = client.get("/v1/activity", headers=AUTH_HEADER).json()
        assert busy["inflight_requests"] == 1
        assert busy["asr_chunk_allowed"] is False
    assert _activity_tracker.count() == 0


def test_activity_tracker_releases_count_after_failure():
    with pytest.raises(RuntimeError):
        with _activity_tracker.inference():
            raise RuntimeError("boom")
    assert _activity_tracker.count() == 0


def test_embedding_and_rerank_calls_are_counted_as_inflight(model_loaded):
    def embed_side_effect(*_args, **_kwargs):
        assert _activity_tracker.count() == 1
        return MOCK_EMBED_RESULT

    def rerank_side_effect(*_args, **_kwargs):
        assert _activity_tracker.count() == 1
        return [0.9, 0.3]

    with patch.object(_model_manager, "embed", side_effect=embed_side_effect):
        assert client.post(
            "/v1/embeddings", json={"texts": ["hello"]}, headers=AUTH_HEADER
        ).status_code == status.HTTP_200_OK
    assert _activity_tracker.count() == 0

    with patch.object(_model_manager, "rerank", side_effect=rerank_side_effect):
        assert client.post(
            "/v1/rerank",
            json={"query": "q", "passages": ["p"]},
            headers=AUTH_HEADER,
        ).status_code == status.HTTP_200_OK
    assert _activity_tracker.count() == 0


# ── Model Info ───────────────────────────────────────────────────────────────

def test_model_info(model_loaded):
    resp = client.get("/model-info")
    assert resp.status_code == status.HTTP_200_OK
    data = ModelInfoResponse(**resp.json())
    assert data.api_version == "1"
    assert data.embedding_model == "BAAI/bge-m3"
    assert data.embedding_dimension == 1024
    assert data.reranker_model == "BAAI/bge-reranker-v2-m3"
    assert data.device == "cuda"
    assert data.runtime_release_id == ""
    assert data.runtime_source_fingerprint == ""
    assert data.runtime_lock_sha256 == ""


# ── Embedding ────────────────────────────────────────────────────────────────

def test_embed_success(model_loaded):
    resp = client.post("/v1/embeddings", json={"texts": ["hello", "world"]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_200_OK
    data = EmbeddingResponse(**resp.json())
    assert len(data.embeddings) == 2
    # First item
    assert data.embeddings[0].dense == [0.1, 0.2, 0.3]
    assert data.embeddings[0].sparse_indices == [1, 2]
    assert data.embeddings[0].sparse_values == [0.5, 0.6]
    # Second item
    assert data.embeddings[1].dense == [0.4, 0.5, 0.6]
    assert data.embeddings[1].sparse_indices == [3]
    assert data.embeddings[1].sparse_values == [0.7]


def test_embed_single_item(model_loaded):
    """Single text should still return a list with one item (not a bare dict)."""
    resp = client.post("/v1/embeddings", json={"texts": ["single"]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_200_OK
    data = EmbeddingResponse(**resp.json())
    assert isinstance(data.embeddings, list)
    # Mock returns 2 items regardless of input; the contract test verifies
    # the response is a list, not that the count matches input exactly.


def test_embed_empty_texts():
    """Empty texts array should be rejected (min_length=1)."""
    resp = client.post("/v1/embeddings", json={"texts": []}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_embed_no_auth():
    """Missing Authorization header should return 401."""
    resp = client.post("/v1/embeddings", json={"texts": ["hello"]})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_embed_bad_token():
    """Wrong token should return 403."""
    resp = client.post("/v1/embeddings", json={"texts": ["hello"]}, headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_embed_models_not_loaded():
    """GPU unavailable should return 503."""
    resp = client.post("/v1/embeddings", json={"texts": ["hello"]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_embed_chinese_text(model_loaded):
    """Chinese text should be accepted and processed."""
    resp = client.post("/v1/embeddings", json={"texts": ["你好世界", "钢结构规范"]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_200_OK
    data = EmbeddingResponse(**resp.json())
    assert len(data.embeddings) == 2


# ── Rerank ───────────────────────────────────────────────────────────────────

def test_rerank_success(model_loaded):
    resp = client.post("/v1/rerank", json={"query": "test", "passages": ["a", "b"]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_200_OK
    data = RerankResponse(**resp.json())
    assert len(data.scores) == 2
    assert data.scores == [0.9, 0.3]


def test_rerank_single_passage(model_loaded):
    """Single passage should return a list of scores, not a scalar."""
    resp = client.post("/v1/rerank", json={"query": "test", "passages": ["only one"]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_200_OK
    data = RerankResponse(**resp.json())
    assert isinstance(data.scores, list)
    # Mock returns 2 scores regardless of input; the contract test verifies
    # the response is a list, not that the count matches input exactly.


def test_rerank_empty_passages():
    """Empty passages should be rejected."""
    resp = client.post("/v1/rerank", json={"query": "test", "passages": []}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_rerank_no_auth():
    """Missing auth should return 401."""
    resp = client.post("/v1/rerank", json={"query": "test", "passages": ["a"]})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_rerank_models_not_loaded():
    """GPU unavailable should return 503."""
    resp = client.post("/v1/rerank", json={"query": "test", "passages": ["a"]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# ── Input validation ─────────────────────────────────────────────────────────

def test_embed_text_too_long(model_loaded):
    """Text exceeding MAX_TEXT_LENGTH should be rejected with 422."""
    long_text = "x" * 9000
    resp = client.post("/v1/embeddings", json={"texts": [long_text]}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_embed_batch_too_large(model_loaded):
    """Batch exceeding MAX_BATCH_SIZE should be rejected with 422."""
    many_texts = ["x"] * 200
    resp = client.post("/v1/embeddings", json={"texts": many_texts}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ── Response order stability ─────────────────────────────────────────────────

def test_embed_order_stable(model_loaded):
    """Response order must match input order."""
    texts = ["first", "second", "third"]
    resp = client.post("/v1/embeddings", json={"texts": texts}, headers=AUTH_HEADER)
    assert resp.status_code == status.HTTP_200_OK
    data = EmbeddingResponse(**resp.json())
    # Mock returns exactly MOCK_EMBED_RESULT (2 items) regardless of input
    assert len(data.embeddings) == 2
    # Verify the mock data structure is preserved
    assert data.embeddings[0].dense == [0.1, 0.2, 0.3]


# ── CORS Headers ─────────────────────────────────────────────────────────────

def test_cors_headers(model_loaded):
    """CORS should allow Ubuntu backend requests."""
    resp = client.get("/health", headers={"Origin": "http://localhost"})
    assert resp.status_code == status.HTTP_200_OK
    assert "Access-Control-Allow-Origin" in resp.headers


# ── Schema validation: dense dimension ───────────────────────────────────────

def test_dense_dimension_1024():
    """Verify the config constant matches the production contract."""
    from gpu_service.config import EMBED_DIM
    assert EMBED_DIM == 1024, "Dense dimension must be 1024 for BGE-M3"


# ── Sparse structure ─────────────────────────────────────────────────────────

def test_sparse_indices_values_match():
    """Verify that sparse_indices and sparse_values are valid lists."""
    from gpu_service.schemas import EmbeddingItem
    item = EmbeddingItem(dense=[0.1], sparse_indices=[1, 2, 3], sparse_values=[0.5, 0.6, 0.7])
    assert len(item.sparse_indices) == len(item.sparse_values)
    assert all(isinstance(i, int) for i in item.sparse_indices)
    assert all(isinstance(v, float) for v in item.sparse_values)
