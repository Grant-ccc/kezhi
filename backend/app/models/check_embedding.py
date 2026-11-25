"""Configuration check by default; --live makes exactly two bounded API requests."""
import argparse
import json

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.embedding import BailianEmbedding, EmbeddingError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="发送两段固定的自行编写文本，最多两次调用、无自动重试；可能计费")
    args = parser.parse_args(argv)
    records = []
    try:
        provider = BailianEmbedding(get_settings())
        if not args.live:
            print(json.dumps({"status": "configuration_present_not_verified", "profile": provider.profile,
                              "network_calls": 0}, ensure_ascii=False))
            return 0
        for kind, text in [("document", "事务内的操作要么全部成功，要么全部回滚。"),
                           ("query", "转账中途失败应该怎么办？")]:
            result = provider.embed([text], kind=kind)
            records.append({"kind": kind, "requested_model": result.requested_model,
                            "vector_count": len(result.vectors), "dimensions": len(result.vectors[0]),
                            "total_tokens": result.total_tokens, "request_id": result.request_id,
                            "elapsed_ms": result.elapsed_ms})
    except (EmbeddingError, ValidationError) as error:
        code = error.code if isinstance(error, EmbeddingError) else "INVALID_LOCAL_CONFIG"
        message = str(error) if isinstance(error, EmbeddingError) else "本地配置格式不正确；请核对 .env.example，不要分享密钥"
        print(json.dumps({"status": "failed", "code": code, "message": message,
                          "completed_calls": records, "billing_note": "失败请求也可能已计费，以服务商账单为准" if args.live else "未发送请求"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "embedding_connectivity_passed", "profile": provider.profile,
                      "calls": records, "scope": "仅验证 API 连通与向量结构；未验证 SeekDB 检索、权限或问答效果"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
