"""Transport stubs verify the adapter contract, never real model quality."""
import json

import httpx
from pydantic import SecretStr, ValidationError
import pytest

from app.core.config import Settings
from app.models import check_embedding
from app.models.embedding import BailianEmbedding, EmbeddingError


@pytest.fixture
def settings():
    return Settings(_env_file=None, db_password="test-only", seekdb_root_password="test-only",
                    jwt_secret="x" * 32, demo_password="test-only",
                    dashscope_api_key=SecretStr("test-key-never-real"),
                    dashscope_workspace_id="workspace-test", embedding_dimensions=256)


def response(count=1):
    return {"output": {"embeddings": [{"index": i, "embedding": [i + 1.0] * 256} for i in range(count)]},
            "usage": {"total_tokens": 17}, "request_id": "test-request-001"}


def test_dimensions_load_from_dotenv(tmp_path, settings):
    path = tmp_path / ".env"
    path.write_text("EMBEDDING_DIMENSIONS=1024\n", encoding="utf-8")
    fields = settings.model_dump(exclude={"embedding_dimensions"})
    assert Settings(_env_file=path, **fields).embedding_dimensions == 1024
    path.write_text("EMBEDDING_DIMENSIONS=123\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings(_env_file=path, **fields)


def test_request_roles_and_reordered_response(settings):
    requests = []

    def handler(request):
        requests.append(request)
        payload = response(2)
        payload["output"]["embeddings"].reverse()
        return httpx.Response(200, json=payload)

    provider = BailianEmbedding(settings, transport=httpx.MockTransport(handler))
    for role in ("document", "query"):
        result = provider.embed(["资料 A", "资料 B"], kind=role)
        body = json.loads(requests[-1].content)
        assert body["parameters"] == {"dimension": 256, "text_type": role, "output_type": "dense"}
        assert body["input"] == {"texts": ["资料 A", "资料 B"]}
        assert body["model"] == "qwen3.7-text-embedding"
        assert result.vectors[0][0] == 1.0 and result.vectors[1][0] == 2.0
        assert result.total_tokens == 17 and result.request_id == "test-request-001"
        assert result.profile == provider.profile
    assert requests[0].url == "https://workspace-test.cn-beijing.maas.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    assert requests[0].headers["authorization"] == "Bearer test-key-never-real"
    assert "test-key-never-real" not in repr(settings)
    assert "vectors=" not in repr(result)


def test_legacy_index_and_unknown_usage(settings):
    payload = response()
    row = payload["output"]["embeddings"][0]
    row["text_index"] = row.pop("index")
    del payload["usage"]
    result = BailianEmbedding(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))).embed(["资料"], kind="document")
    assert result.total_tokens is None  # unknown, never a fabricated zero


@pytest.mark.parametrize("texts,kind", [([], "query"), (["a"] * 9, "document"), ("text", "query"),
                                      ([" "], "query"), (["a" * 601], "document"), ([None], "query"),
                                      (["valid"], "other")])
def test_invalid_input_never_sends(settings, texts, kind):
    def handler(_):
        pytest.fail("Invalid input must be rejected before any network call")
    with pytest.raises(EmbeddingError) as caught:
        BailianEmbedding(settings, transport=httpx.MockTransport(handler)).embed(texts, kind=kind)
    assert caught.value.code == "EMBEDDING_INVALID_INPUT"


@pytest.mark.parametrize("field,value,code", [
    ("dashscope_api_key", None, "EMBEDDING_NOT_CONFIGURED"),
    ("dashscope_workspace_id", "", "EMBEDDING_NOT_CONFIGURED"),
    ("dashscope_workspace_id", "https://untrusted.example/path", "EMBEDDING_INVALID_CONFIG"),
])
def test_missing_and_invalid_config(settings, field, value, code):
    with pytest.raises(EmbeddingError) as caught:
        BailianEmbedding(settings.model_copy(update={field: value}))
    assert caught.value.code == code


@pytest.mark.parametrize("status,code,retryable", [
    (401, "EMBEDDING_ACCESS_DENIED", False), (403, "EMBEDDING_ACCESS_DENIED", False),
    (429, "EMBEDDING_RATE_LIMITED", True), (503, "EMBEDDING_UNAVAILABLE", True),
    (400, "EMBEDDING_REJECTED", False), (302, "EMBEDDING_REJECTED", False),
])
def test_upstream_errors_are_redacted_and_not_retried(settings, status, code, retryable):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://untrusted.example"},
                              text="secret-key PRIVATE_DOCUMENT upstream internals")
    with pytest.raises(EmbeddingError) as caught:
        BailianEmbedding(settings, transport=httpx.MockTransport(handler)).embed(["PRIVATE_DOCUMENT"], kind="document")
    assert caught.value.code == code and caught.value.retryable == retryable
    assert len(calls) == 1
    assert "secret-key" not in str(caught.value) and "PRIVATE_DOCUMENT" not in str(caught.value)


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "EMBEDDING_TIMEOUT"), (httpx.ConnectError, "EMBEDDING_NETWORK")])
def test_network_errors(settings, error, code):
    calls = []
    def handler(request):
        calls.append(request)
        raise error("secret-key private input", request=request)
    with pytest.raises(EmbeddingError) as caught:
        BailianEmbedding(settings, transport=httpx.MockTransport(handler)).embed(["资料"], kind="document")
    assert caught.value.code == code and caught.value.retryable
    assert len(calls) == 1 and "secret-key" not in str(caught.value)


@pytest.mark.parametrize("problem", ["missing", "duplicate", "range", "bool-index", "conflict-index", "dimension", "zero", "nan", "infinity", "bool-value", "token-count", "request-id", "upstream-code", "not-object", "not-json"])
def test_malformed_vectors_are_not_accepted(settings, problem):
    payload = response(2)
    rows = payload["output"]["embeddings"]
    if problem == "missing": rows.pop()
    elif problem == "duplicate": rows[1]["index"] = 0
    elif problem == "range": rows[1]["index"] = 2
    elif problem == "bool-index": rows[0]["index"] = False
    elif problem == "conflict-index": rows[0]["text_index"] = 1
    elif problem == "dimension": rows[0]["embedding"].pop()
    elif problem == "zero": rows[0]["embedding"] = [0] * 256
    elif problem == "nan": rows[0]["embedding"][0] = float("nan")
    elif problem == "infinity": rows[0]["embedding"][0] = float("inf")
    elif problem == "bool-value": rows[0]["embedding"][0] = True
    elif problem == "token-count": payload["usage"]["total_tokens"] = -1
    elif problem == "request-id": payload["request_id"] = "PRIVATE DOCUMENT CONTENT"
    elif problem == "upstream-code": payload["code"] = "InternalError"
    elif problem == "not-object": payload = []
    content = "not json" if problem == "not-json" else json.dumps(payload)
    with pytest.raises(EmbeddingError) as caught:
        BailianEmbedding(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))).embed(["A", "B"], kind="document")
    assert caught.value.code == "EMBEDDING_INVALID_RESPONSE"


def test_check_is_offline_by_default(settings, monkeypatch, capsys):
    monkeypatch.setattr(check_embedding, "get_settings", lambda: settings)
    monkeypatch.setattr(BailianEmbedding, "embed", lambda *a, **kw: pytest.fail("Configuration check cannot call the API"))
    assert check_embedding.main([]) == 0
    assert json.loads(capsys.readouterr().out)["network_calls"] == 0


def test_live_smoke_stops_and_preserves_completed_call(settings, monkeypatch, capsys):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 2: return httpx.Response(429, text="sensitive upstream text")
        return httpx.Response(200, json=response())
    monkeypatch.setattr(check_embedding, "get_settings", lambda: settings)
    monkeypatch.setattr(check_embedding, "BailianEmbedding", lambda cfg: BailianEmbedding(cfg, transport=httpx.MockTransport(handler)))
    assert check_embedding.main(["--live"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert len(calls) == 2 and len(output["completed_calls"]) == 1
    assert output["completed_calls"][0]["total_tokens"] == 17
    assert "sensitive" not in str(output)
