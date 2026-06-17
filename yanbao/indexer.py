import json
import logging
import multiprocessing
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from . import db as db_module
from .config import CORRUPTED_PDFS_PATH, INDEX_STATE_PATH
from .extractor import get_page_count, chunk_text, try_extract
from .scanner import scan_all, find_new_and_changed

logging.getLogger("pypdf").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

VEC_TABLE = "chunk_vectors"


def _load_state() -> dict:
    if INDEX_STATE_PATH.exists():
        with open(INDEX_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"phase": "metadata", "completed_pdfs": [], "errors": []}


def _save_state(state: dict):
    INDEX_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _worker_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _worker_process_pdf(batch: list[tuple]) -> list[tuple]:
    results = []
    for code, pdf_path in batch:
        text, err = try_extract(pdf_path)
        if text is None:
            results.append(("error", code, pdf_path, err or "unknown"))
            continue
        page_count = get_page_count(pdf_path)
        chunks = chunk_text(text)
        results.append(("ok", code, page_count, chunks))
    return results


def index_metadata(conn, records: list[dict], reset: bool = False) -> int:
    inserted = 0
    for rec in records:
        existing = conn.execute(
            "SELECT page_count FROM reports WHERE info_code=?", (rec["info_code"],)
        ).fetchone()
        page_count = 0 if reset else (existing[0] if existing else 0)
        conn.execute(
            """INSERT OR REPLACE INTO reports
               (info_code, title, org, stock, stock_code, industry,
                em_rating, authors, report_date, report_url, pdf_url,
                pdf_path, json_path, file_hash, page_count, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["info_code"],
                rec["title"],
                rec["org"],
                rec["stock"],
                rec["stock_code"],
                rec["industry"],
                rec["em_rating"],
                rec["authors"],
                rec["report_date"],
                rec["report_url"],
                rec["pdf_url"],
                rec["pdf_path"],
                rec["json_path"],
                rec["file_hash"],
                page_count,
                datetime.now().isoformat(),
            ),
        )
        inserted += 1
    conn.commit()
    logger.info(f"Indexed {inserted} metadata records")
    return inserted


def index_pdf_content(conn, info_code: str, pdf_path: str) -> int:
    text, err = try_extract(pdf_path)
    if text is None:
        if err:
            logger.warning(f"Corrupted PDF: {pdf_path} ({err})")
            conn.execute("UPDATE reports SET page_count=-1 WHERE info_code=?", (info_code,))
            _log_corrupted_pdf(info_code, pdf_path, err)
            conn.commit()
        return 0
    page_count = get_page_count(pdf_path)
    conn.execute("UPDATE reports SET page_count=? WHERE info_code=?", (page_count, info_code))
    chunks = chunk_text(text)
    if not chunks:
        return 0
    chunk_id_start = conn.execute(
        "SELECT COALESCE(MAX(chunk_id), 0) + 1 FROM text_chunks"
    ).fetchone()[0]
    chunk_data = []
    for i, (c_text, c_len) in enumerate(chunks):
        chunk_id = chunk_id_start + i
        chunk_data.append((chunk_id, info_code, i, c_text, c_len))
    conn.executemany(
        "INSERT INTO text_chunks(chunk_id, info_code, chunk_index, chunk_text, token_count) VALUES (?,?,?,?,?)",
        chunk_data,
    )
    conn.commit()
    return len(chunks)


def index_vectors(conn, info_code: str) -> int:
    from .embeddings import encode
    rows = conn.execute(
        "SELECT chunk_id, chunk_text FROM text_chunks WHERE info_code=? ORDER BY chunk_index",
        (info_code,),
    ).fetchall()
    if not rows:
        return 0
    texts = [r["chunk_text"] for r in rows]
    chunk_ids = [r["chunk_id"] for r in rows]
    embeddings = encode(texts)
    if len(embeddings) == 0:
        return 0
    vec_data = []
    for cid, emb in zip(chunk_ids, embeddings):
        vec_str = "[" + ",".join(f"{v:.6f}" for v in emb) + "]"
        vec_data.append((cid, vec_str))
    conn.executemany(
        f"INSERT INTO {VEC_TABLE}(chunk_id, embedding) VALUES (?,?)", vec_data
    )
    conn.commit()
    return len(rows)


def run_metadata_only(conn):
    logger.info("Starting metadata-only index...")
    records = scan_all()
    logger.info(f"Found {len(records)} total records")
    meta_inserted = index_metadata(conn, records)
    stats = db_module.get_stats(conn)
    logger.info(f"Metadata done: {stats}")
    _save_state({
        "phase": "metadata",
        "last_run": datetime.now().isoformat(),
        "stats": stats,
    })
    return records


def run_full_index(conn, reset: bool = False):
    logger.info("Starting full index..." + (" (reset)" if reset else " (resume)"))
    state = _load_state()
    records = scan_all()
    logger.info(f"Found {len(records)} total records")
    meta_inserted = index_metadata(conn, records, reset=reset)
    logger.info(f"Metadata phase done: {meta_inserted} records")
    _run_pdf_phase(conn, state, 1)
    _run_vector_phase(conn, state)


def _log_corrupted_pdf(code: str, pdf_path: str, error: str):
    entry = {
        "code": code,
        "pdf_path": pdf_path,
        "error": error,
        "timestamp": datetime.now().isoformat(),
    }
    CORRUPTED_PDFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CORRUPTED_PDFS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_full_index_parallel(conn, num_workers: int = 8, reset: bool = False):
    logger.info(f"Starting parallel full index with {num_workers} workers..."
                + (" (reset)" if reset else " (resume)"))
    state = _load_state()
    records = scan_all()
    logger.info(f"Found {len(records)} total records")
    meta_inserted = index_metadata(conn, records, reset=reset)
    logger.info(f"Metadata phase done: {meta_inserted} records")

    to_process = conn.execute(
        "SELECT info_code, pdf_path FROM reports WHERE pdf_path != '' AND page_count = 0"
    ).fetchall()
    items = [(r["info_code"], r["pdf_path"]) for r in to_process]
    total_pdf = len(items)
    logger.info(f"PDF extraction needed for {total_pdf} reports")

    state = _load_state()
    _save_state({**state, "phase": "pdf_extraction", "total": total_pdf, "done": 0,
                 "workers": num_workers, "last_update": datetime.now().isoformat()})

    batch_size = max(30, len(items) // (num_workers * 40))
    n_splits = (len(items) + batch_size - 1) // batch_size
    batches = []
    for i in range(n_splits):
        start = i * batch_size
        end = start + batch_size
        batches.append(items[start:end])

    pdf_ok = 0
    start_time = time.time()
    pool = multiprocessing.Pool(num_workers, initializer=_worker_init)
    async_results = [pool.apply_async(_worker_process_pdf, (b,)) for b in batches]
    pending = set(range(len(async_results)))

    try:
        while pending:
            newly_done = [i for i in pending if async_results[i].ready()]
            for i in newly_done:
                pending.remove(i)
                for result in async_results[i].get():
                    if result[0] == "ok":
                        _, code, page_count, chunks = result
                        conn.execute(
                            "UPDATE reports SET page_count=? WHERE info_code=?",
                            (page_count, code),
                        )
                        if chunks:
                            cid_start = conn.execute(
                                "SELECT COALESCE(MAX(chunk_id), 0) + 1 FROM text_chunks"
                            ).fetchone()[0]
                            chunk_rows = []
                            for i, (ct, clen) in enumerate(chunks):
                                chunk_rows.append((cid_start + i, code, i, ct, clen))
                            conn.executemany(
                                "INSERT INTO text_chunks(chunk_id, info_code, chunk_index, chunk_text, token_count) VALUES (?,?,?,?,?)",
                                chunk_rows,
                            )
                        pdf_ok += 1
                    elif result[0] == "error":
                        _, code, pdf_path, err = result
                        logger.warning(f"Corrupted PDF: {pdf_path} ({err})")
                        conn.execute(
                            "UPDATE reports SET page_count=-1 WHERE info_code=?",
                            (code,),
                        )
                        _log_corrupted_pdf(code, pdf_path, err)

                if pdf_ok % 50 == 0:
                    conn.commit()
                    elapsed = time.time() - start_time
                    rate = pdf_ok / elapsed if elapsed > 0 else 0
                    remaining = (total_pdf - pdf_ok) / rate if rate > 0 else 0
                    progress = pdf_ok / total_pdf * 100
                    logger.info(
                        f"PDF extraction: {pdf_ok}/{total_pdf} ({progress:.1f}%) "
                        f"[{rate:.1f}/min, est {remaining/60:.0f}min]"
                    )
                    _save_state({**state, "phase": "pdf_extraction", "total": total_pdf,
                                 "done": pdf_ok, "rate": round(rate, 1),
                                 "eta_min": round(remaining / 60, 1),
                                 "last_update": datetime.now().isoformat()})

            if not pending:
                break
            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.warning("Received Ctrl+C, terminating workers and saving state...")
        pool.terminate()
        pool.close()
        pool.join()
        conn.commit()
        elapsed = time.time() - start_time
        _save_state({**state, "phase": "interrupted", "total": total_pdf,
                     "done": pdf_ok, "elapsed": round(elapsed, 1),
                     "last_update": datetime.now().isoformat()})
        logger.warning(f"Index interrupted after {pdf_ok}/{total_pdf} PDFs")
        sys.exit(1)
    finally:
        pool.terminate()
        pool.close()
        pool.join()

    conn.commit()
    logger.info(f"PDF extraction done: {pdf_ok} reports ({total_pdf - pdf_ok} skipped/corrupted)")

    _run_vector_phase(conn, state)


def _run_pdf_phase(conn, state, num_workers: int = 1):
    to_process = conn.execute(
        "SELECT info_code, pdf_path FROM reports WHERE pdf_path != '' AND page_count = 0"
    ).fetchall()
    total_pdf = len(to_process)
    if total_pdf == 0:
        logger.info("No PDFs to extract")
        return
    logger.info(f"PDF extraction needed for {total_pdf} reports")
    _save_state({**state, "phase": "pdf_extraction", "total": total_pdf, "done": 0,
                 "last_update": datetime.now().isoformat()})

    pdf_ok = 0
    start_time = time.time()
    try:
        for row in to_process:
            n = index_pdf_content(conn, row["info_code"], row["pdf_path"])
            if n > 0:
                pdf_ok += 1
            if pdf_ok % 50 == 0 and pdf_ok > 0:
                conn.commit()
                elapsed = time.time() - start_time
                rate = pdf_ok / elapsed * 60
                remaining = (total_pdf - pdf_ok) / (pdf_ok / elapsed) if pdf_ok > 0 else 0
                progress = pdf_ok / total_pdf * 100
                logger.info(
                    f"PDF extraction: {pdf_ok}/{total_pdf} ({progress:.1f}%) "
                    f"[{rate:.0f}/min, est {remaining/60:.0f}min]"
                )
                _save_state({**state, "phase": "pdf_extraction", "total": total_pdf,
                             "done": pdf_ok, "rate": round(rate, 1),
                             "eta_min": round(remaining / 60, 1),
                             "last_update": datetime.now().isoformat()})
    except KeyboardInterrupt:
        logger.warning("Received Ctrl+C, saving state...")
        conn.commit()
        _save_state({**state, "phase": "interrupted", "total": total_pdf,
                     "done": pdf_ok, "last_update": datetime.now().isoformat()})
        sys.exit(1)
    conn.commit()
    logger.info(f"PDF extraction done: {pdf_ok} reports")


def _run_vector_phase(conn, state):
    from .embeddings import is_available
    if not is_available():
        logger.warning("Embedding model unavailable, skipping vector generation")
        stats = db_module.get_stats(conn)
        _save_state({"phase": "complete", "note": "vectors skipped",
                     "last_run": datetime.now().isoformat(), "stats": stats})
        return
    logger.info("Starting vector generation...")
    _save_state({**state, "phase": "vector_generation", "last_update": datetime.now().isoformat()})
    try:
        to_vec = conn.execute(
            """SELECT DISTINCT tc.info_code FROM text_chunks tc
               LEFT JOIN chunk_vectors cv ON tc.chunk_id = cv.chunk_id
               WHERE cv.chunk_id IS NULL"""
        ).fetchall()
        total_vec = len(to_vec)
        logger.info(f"Vector generation needed for {total_vec} reports")
        _save_state({**state, "phase": "vector_generation", "total": total_vec,
                     "done": 0, "last_update": datetime.now().isoformat()})
        vec_ok = 0
        start_time = time.time()
        for idx, row in enumerate(to_vec):
            n = index_vectors(conn, row["info_code"])
            if n > 0:
                vec_ok += 1
            if idx % 5 == 0:
                time.sleep(0.001)
            if vec_ok % 50 == 0 and vec_ok > 0:
                conn.commit()
                elapsed = time.time() - start_time
                rate = vec_ok / elapsed * 60
                remaining = (total_vec - vec_ok) / (vec_ok / elapsed) if vec_ok > 0 else 0
                progress = vec_ok / total_vec * 100
                logger.info(f"Vectors: {vec_ok}/{total_vec} ({progress:.1f}%) [{rate:.0f}/min, est {remaining/60:.0f}min]")
                _save_state({**state, "phase": "vector_generation", "total": total_vec,
                             "done": vec_ok, "rate": round(rate, 1),
                             "eta_min": round(remaining / 60, 1),
                             "last_update": datetime.now().isoformat()})
        logger.info(f"Vector generation done: {vec_ok} reports")
    except KeyboardInterrupt:
        logger.warning("Received Ctrl+C during vector generation, exiting...")
        conn.commit()
        _save_state({**state, "phase": "interrupted", "note": "vector generation",
                     "last_update": datetime.now().isoformat()})
        sys.exit(1)
    except Exception as e:
        logger.warning(f"Vector generation failed: {e}")
    stats = db_module.get_stats(conn)
    logger.info(f"Final stats: {stats}")
    _save_state({
        "phase": "complete",
        "last_run": datetime.now().isoformat(),
        "stats": stats,
    })


def run_incremental(conn):
    logger.info("Starting incremental index...")
    records = scan_all()
    new_recs, changed_recs = find_new_and_changed(conn, records)
    logger.info(f"New: {len(new_recs)}, Changed: {len(changed_recs)}")
    all_to_process = new_recs + changed_recs
    if not all_to_process:
        logger.info("No changes detected")
        return
    index_metadata(conn, all_to_process)
    for rec in all_to_process:
        code = rec["info_code"]
        if rec["pdf_path"]:
            n = index_pdf_content(conn, code, rec["pdf_path"])
            if n > 0:
                index_vectors(conn, code)
    logger.info(f"Incremental index done: {len(all_to_process)} reports")
