import hashlib
import json
import logging
from pathlib import Path

from .config import DATA_DIR

logger = logging.getLogger(__name__)


def _parse_json(path: Path) -> tuple[dict | None, bytes]:
    raw = path.read_bytes()
    encodings = ["utf-8", "gbk", "gb18030"]
    for enc in encodings:
        try:
            return json.loads(raw.decode(enc)), raw
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    try:
        import chardet
        det = chardet.detect(raw)
        if det["encoding"] and det["confidence"] > 0.5:
            return json.loads(raw.decode(det["encoding"])), raw
    except Exception:
        pass
    logger.warning(f"Failed to decode JSON: {path}")
    return None, raw


def scan_all() -> list[dict]:
    results = []
    for entry in sorted(DATA_DIR.iterdir()):
        if not entry.is_dir():
            continue
        for fpath in sorted(entry.iterdir()):
            if fpath.suffix.lower() != ".json":
                continue
            meta, raw = _parse_json(fpath)
            if meta is None:
                continue
            pdf_path = fpath.with_suffix(".pdf")
            results.append({
                "info_code": (meta.get("infoCode") or "").strip(),
                "title": (meta.get("title") or "").strip(),
                "org": (meta.get("org") or "").strip(),
                "stock": (meta.get("stock") or "").strip(),
                "stock_code": (meta.get("stockCode") or "").strip(),
                "industry": (meta.get("industry") or "").strip(),
                "em_rating": (meta.get("emRating") or "").strip(),
                "authors": json.dumps(meta.get("authors", []), ensure_ascii=False),
                "report_date": (meta.get("date") or "").strip(),
                "report_url": (meta.get("reportUrl") or "").strip(),
                "pdf_url": (meta.get("pdfUrl") or "").strip(),
                "pdf_path": str(pdf_path) if pdf_path.exists() else "",
                "json_path": str(fpath),
                "file_hash": hashlib.md5(raw).hexdigest(),
            })
    return results


def find_new_and_changed(conn, records: list[dict]) -> tuple[list[dict], list[dict]]:
    existing = {
        r[0]: r[1]
        for r in conn.execute("SELECT info_code, file_hash FROM reports").fetchall()
    }
    new_records = []
    changed_records = []
    for rec in records:
        code = rec["info_code"]
        if code not in existing:
            new_records.append(rec)
        elif existing[code] != rec["file_hash"]:
            changed_records.append(rec)
    return new_records, changed_records
