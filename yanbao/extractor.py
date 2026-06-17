import logging
from pathlib import Path

from pypdf import PdfReader

from .config import CHUNK_SIZE, CHUNK_OVERLAP

logger = logging.getLogger(__name__)


def extract_text(pdf_path: str | Path) -> str | None:
    path = Path(pdf_path)
    if not path.exists():
        return None
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n".join(pages) if pages else None
    except Exception as e:
        logger.warning(f"Failed to extract PDF {path.name}: {e}")
        return None


def try_extract(pdf_path: str | Path) -> tuple[str | None, str | None]:
    path = Path(pdf_path)
    if not path.exists():
        return None, "file not found"
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        if not pages:
            return None, "no text extracted"
        return "\n".join(pages), None
    except Exception as e:
        return None, str(e)


def get_page_count(pdf_path: str | Path) -> int:
    path = Path(pdf_path)
    if not path.exists():
        return 0
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def chunk_text(text: str) -> list[tuple[str, int]]:
    if not text:
        return []
    paragraphs = text.split("\n\n")
    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para)

        if current_len + para_len <= CHUNK_SIZE:
            current.append(para)
            current_len += para_len
        else:
            if current:
                combined = "\n\n".join(current)
                chunks.append((combined, current_len))
            current = [para]
            current_len = para_len

    if current:
        combined = "\n\n".join(current)
        chunks.append((combined, current_len))

    return chunks
