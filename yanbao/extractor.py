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
                pages.append(_clean(text))
        return "\n".join(pages) if pages else None
    except Exception as e:
        logger.warning(f"Failed to extract PDF {path.name}: {e}")
        return None


def _clean(text: str) -> str:
    return text.encode("utf-8", errors="replace").decode("utf-8")


def try_extract(pdf_path: str | Path) -> tuple[str | None, str | None, int]:
    path = Path(pdf_path)
    if not path.exists():
        return None, "file not found", 0
    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(_clean(text))
        if not pages:
            return None, "no text extracted", page_count
        return "\n".join(pages), None, page_count
    except Exception as e:
        return None, str(e), 0


def get_page_count(pdf_path: str | Path) -> int:
    path = Path(pdf_path)
    if not path.exists():
        return 0
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def _build_overlap(prev_paras: list[str]) -> list[str]:
    if CHUNK_OVERLAP <= 0 or not prev_paras:
        return []
    overlap = []
    overlap_len = 0
    for p in reversed(prev_paras):
        if overlap_len + len(p) <= CHUNK_OVERLAP:
            overlap.insert(0, p)
            overlap_len += len(p)
        else:
            break
    if not overlap:
        return [prev_paras[-1]]
    return overlap


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
            overlap = _build_overlap(current)
            overlap_len = sum(len(p) for p in overlap)
            current = overlap + [para]
            current_len = overlap_len + para_len

    if current:
        combined = "\n\n".join(current)
        chunks.append((combined, current_len))

    return chunks
