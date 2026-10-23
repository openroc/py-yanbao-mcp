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


def _split_long(text: str, limit: int) -> list[str]:
    """把超长段落切成不超过 limit 字符的片段。

    优先在换行/句读处断开，断不开才硬切。
    """
    if len(text) <= limit:
        return [text]

    pieces = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = 0
        for sep in ("\n", "。", "！", "？", "；", "，", ". ", " "):
            idx = window.rfind(sep)
            if idx > limit // 2:
                cut = idx + len(sep)
                break
        if cut <= 0:
            cut = limit
        piece = remaining[:cut].strip()
        if piece:
            pieces.append(piece)
        remaining = remaining[cut:]

    tail = remaining.strip()
    if tail:
        pieces.append(tail)
    return pieces


def _render(segments: list[tuple[str, str]]) -> tuple[str, int]:
    """把 (文本, 前导分隔符) 片段序列渲染成 chunk 文本。

    片段序列中第一个片段的前导分隔符被丢弃（chunk 不以空白开头）。
    """
    if not segments:
        return "", 0
    parts = [segments[0][0]]
    for text, sep in segments[1:]:
        parts.append(sep)
        parts.append(text)
    rendered = "".join(parts)
    return rendered, len(rendered)


def _build_overlap(prev_segments: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    """从上一 chunk 尾部取若干片段作为重叠，且必须放得下新片段。

    budget 是留给重叠块的总字符数（含其内部与后继的分隔符）。
    """
    if CHUNK_OVERLAP <= 0 or not prev_segments or budget <= 0:
        return []

    overlap: list[tuple[str, str]] = []
    text_len = 0
    contrib = 0
    for seg in reversed(prev_segments):
        text = seg[0]
        delta = len(text) + (len(overlap[0][1]) if overlap else 0)
        if contrib + delta > budget:
            break
        if text_len + len(text) > CHUNK_OVERLAP:
            break
        overlap.insert(0, seg)
        contrib += delta
        text_len += len(text)

    if not overlap:
        # 上一 chunk 的末片段本身就超长，只取它的尾部
        take = min(budget, CHUNK_OVERLAP)
        if take > 0:
            overlap = [(prev_segments[-1][0][-take:], "")]
    return overlap


def chunk_text(text: str) -> list[tuple[str, int]]:
    """把文本切成不超过 CHUNK_SIZE 字符的块。

    CHUNK_SIZE 同时是 embedding 模型的 token 上限对应的字符上限，
    因此每个 chunk 必须硬性满足长度约束，不能依赖段落边界恰好对齐。
    """
    if not text:
        return []

    segments: list[tuple[str, str]] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        for i, piece in enumerate(_split_long(para, CHUNK_SIZE)):
            segments.append((piece, "\n\n" if i == 0 else "\n"))

    chunks: list[tuple[str, int]] = []
    current: list[tuple[str, str]] = []
    current_len = 0

    for seg in segments:
        seg_text, seg_sep = seg
        if not current:
            current = [seg]
            current_len = len(seg_text)
            continue

        candidate_len = current_len + len(seg_sep) + len(seg_text)
        if candidate_len <= CHUNK_SIZE:
            current.append(seg)
            current_len = candidate_len
            continue

        rendered, current_len = _render(current)
        chunks.append((rendered, current_len))

        overlap = _build_overlap(current, CHUNK_SIZE - len(seg_sep) - len(seg_text))
        current = overlap + [seg]
        current_len = _render(current)[1]

    if current:
        chunks.append(_render(current))

    return chunks
