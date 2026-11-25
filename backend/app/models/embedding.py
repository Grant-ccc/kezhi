"""Bounded DashScope HTTP adapter. No automatic retries or document publication."""
from dataclasses import dataclass, field
import math
import re
from time import monotonic
from typing import Literal, Protocol, Sequence

import httpx

from app.core.config import Settings

TextKind = Literal["document", "query"]
MAX_BATCH_SIZE = 8
MAX_TEXT_CHARACTERS = 600
PATH = "/services/embeddings/text-embedding/text-embedding"


class EmbeddingError(Exception):
    """Only fixed messages are exposed; never include upstream bodies or inputs."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...] = field(repr=False)
    profile: str
    requested_model: str
    total_tokens: int | None
    request_id: str | None
    elapsed_ms: int


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str], *, kind: TextKind) -> EmbeddingBatch: ...


class BailianEmbedding:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        key = settings.dashscope_api_key
        self._key = key.get_secret_value().strip() if key else ""
        workspace = settings.dashscope_workspace_id.strip()
        if not self._key or not workspace:
            raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", "请在本地 .env 配置百炼 API Key 与业务空间 ID")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", workspace) or self._key.startswith("replace-"):
            raise EmbeddingError("EMBEDDING_INVALID_CONFIG", "请核对百炼业务空间 ID 和密钥配置")
        self._base_url = f"https://{workspace}.{settings.dashscope_region}.maas.aliyuncs.com/api/v1"
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._timeout = settings.embedding_timeout_seconds
        self._transport = transport
        # Query/document roles are paired in this profile; no local normalization or instruction.
        self.profile = f"bailian:{settings.dashscope_region}:{self._model}:{self._dimensions}:dense:query-document-v1:raw"

    def embed(self, texts: Sequence[str], *, kind: TextKind) -> EmbeddingBatch:
        if kind not in {"query", "document"}:
            raise EmbeddingError("EMBEDDING_INVALID_INPUT", "向量化文本角色必须为 query 或 document")
        if isinstance(texts, (str, bytes)) or not 1 <= len(texts) <= MAX_BATCH_SIZE:
            raise EmbeddingError("EMBEDDING_INVALID_INPUT", "每批需要 1 至 8 段文本")
        if any(not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARACTERS for text in texts):
            raise EmbeddingError("EMBEDDING_INVALID_INPUT", "每段需要 1 至 600 个字符且不能仅含空白；不会自动截断")
        body = {"model": self._model, "input": {"texts": list(texts)}, "parameters": {
            "dimension": self._dimensions, "text_type": kind, "output_type": "dense",
        }}
        start = monotonic()
        try:
            # Redirects stay disabled, so credentials cannot follow a redirect to another host.
            with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=False) as client:
                response = client.post(self._base_url + PATH, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException:
            raise EmbeddingError("EMBEDDING_TIMEOUT", "向量化请求超时，请稍后手动重试", retryable=True) from None
        except httpx.RequestError:
            raise EmbeddingError("EMBEDDING_NETWORK", "无法连接向量化服务，请检查网络后手动重试", retryable=True) from None
        status = response.status_code
        if status in {401, 403}:
            raise EmbeddingError("EMBEDDING_ACCESS_DENIED", "百炼密钥、地域或模型访问权限不匹配")
        if status == 429:
            raise EmbeddingError("EMBEDDING_RATE_LIMITED", "向量化服务限流，请稍后手动重试", retryable=True)
        if status >= 500 or status in {408, 425}:
            raise EmbeddingError("EMBEDDING_UNAVAILABLE", "向量化服务暂时不可用，请稍后手动重试", retryable=True)
        if status != 200:
            raise EmbeddingError("EMBEDDING_REJECTED", "向量化请求被拒绝，请检查配置、额度及输入限制")
        try:
            payload = response.json()
            vectors, total_tokens, request_id = self._parse(payload, len(texts))
        except (ValueError, TypeError, KeyError, OverflowError):
            raise EmbeddingError("EMBEDDING_INVALID_RESPONSE", "向量化返回的数据不完整或格式不正确") from None
        return EmbeddingBatch(vectors, self.profile, self._model, total_tokens, request_id,
                              round((monotonic() - start) * 1000))

    def _parse(self, payload: dict, count: int):
        if not isinstance(payload, dict) or payload.get("code") or not isinstance(payload.get("output"), dict):
            raise ValueError
        rows = payload["output"].get("embeddings")
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError
        ordered = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError
            # The current docs use index; older DashScope gateways use text_index.
            index = row.get("index", row.get("text_index"))
            if (type(index) is not int or index not in range(count) or index in ordered
                    or ("text_index" in row and row["text_index"] != index)):
                raise ValueError
            vector = row.get("embedding")
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                raise ValueError
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise ValueError
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or norm == 0:
                raise ValueError
            ordered[index] = tuple(float(value) for value in vector)
        usage = payload.get("usage")
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        if total is not None and (type(total) is not int or total < 0):
            raise ValueError
        request_id = payload.get("request_id")
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)):
            raise ValueError
        return tuple(ordered[index] for index in range(count)), total, request_id
