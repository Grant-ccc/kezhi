"""Validate supported documents without rendering uploaded markup or selecting models."""
from io import BytesIO
from pathlib import PurePosixPath
import re

from pypdf import PdfReader
from app.core.config import get_settings
from app.core.errors import BusinessError

MAX_TEXT_CHARS = 1_000_000


def validate_file(filename: str, data: bytes) -> tuple[str, str]:
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name)
    if not name or len(name) > 200:
        raise BusinessError(422, "INVALID_FILENAME", "文件名为空或过长")
    if not data:
        raise BusinessError(422, "EMPTY_FILE", "不能上传空文件")
    if len(data) > get_settings().max_upload_bytes:
        raise BusinessError(413, "FILE_TOO_LARGE", "文件超过允许大小（默认 10 MiB）")
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in {".txt", ".md", ".pdf"}:
        raise BusinessError(415, "UNSUPPORTED_FILE", "请上传文本型 PDF、UTF-8 TXT 或 Markdown 文件")
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise BusinessError(415, "FILE_TYPE_MISMATCH", "文件内容不是有效的 PDF")
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise BusinessError(415, "ENCRYPTED_PDF", "暂不支持加密 PDF，请先解密再上传")
            if len(reader.pages) > 200:
                raise BusinessError(413, "PDF_TOO_LONG", "PDF 最多支持 200 页")
            length = 0
            for page in reader.pages:
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > 2 * 1024 * 1024:
                    raise BusinessError(413, "PDF_PAGE_TOO_COMPLEX", "PDF 页面过于复杂，请拆分文件后上传")
                length += len((page.extract_text() or "").strip())
                if length > MAX_TEXT_CHARS:
                    raise BusinessError(413, "TEXT_TOO_LONG", "正文过长，请按章节拆分后上传")
            if not length:
                raise BusinessError(422, "NO_EXTRACTABLE_TEXT", "没有可提取正文，扫描件暂不支持，请使用文本型 PDF")
        except BusinessError:
            raise
        except Exception:
            raise BusinessError(422, "INVALID_PDF", "PDF 解析失败，请检查文件是否完整") from None
        return name, "application/pdf"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BusinessError(415, "INVALID_TEXT_ENCODING", "文本文件须使用 UTF-8 编码") from None
    if data.startswith((b"%PDF-", b"PK\x03\x04")) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        raise BusinessError(415, "FILE_TYPE_MISMATCH", "文件内容与文本类型不匹配")
    if not text.strip():
        raise BusinessError(422, "NO_EXTRACTABLE_TEXT", "文件没有可用正文")
    if len(text) > MAX_TEXT_CHARS:
        raise BusinessError(413, "TEXT_TOO_LONG", "正文过长，请按章节拆分后上传")
    return name, "text/markdown" if suffix == ".md" else "text/plain"
