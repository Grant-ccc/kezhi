"""Character-based preparation only. Model token limits are enforced in a later stage."""
from dataclasses import dataclass
import re

from app.documents.extraction import TextPage

CHUNKING_PROFILE = "paragraph-char-v1:600:80"


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    page_number: int | None
    section_title: str | None
    char_start: int
    char_end: int


def split_pages(pages: list[TextPage], *, markdown: bool = False, size: int = 600, overlap: int = 80) -> list[Chunk]:
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("Chunk size must exceed nonnegative overlap")
    chunks = []
    for page in pages:
        text = page.text
        headings = list(re.finditer(r"^#{1,6}[ \t]+(.+)$", text, re.MULTILINE)) if markdown else []
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundary = text.rfind("\n\n", start + size // 2, end)
                if boundary >= 0:
                    end = boundary + 2
                else:
                    boundary = text.rfind("\n", start + size // 2, end)
                    if boundary >= 0:
                        end = boundary + 1
            raw = text[start:end]
            content = raw.strip()
            if content:
                actual_start = start + len(raw) - len(raw.lstrip())
                actual_end = end - (len(raw) - len(raw.rstrip()))
                titles = [heading.group(1).strip() for heading in headings if heading.start() <= actual_start]
                chunks.append(Chunk(len(chunks), content, page.page_number, titles[-1][:255] if titles else None, actual_start, actual_end))
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks
