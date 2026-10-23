import json
import logging
import multiprocessing
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from . import db as db_module
from .config import CORRUPTED_PDFS_PATH, INDEX_STATE_PATH, FACTOR_VEC_TABLE_NAME
from .extractor import chunk_text, try_extract
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
        text, err, page_count = try_extract(pdf_path)
        if text is None:
            results.append(("error", code, pdf_path, err or "unknown"))
            continue
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
            """INSERT INTO reports
               (info_code, title, org, stock, stock_code, industry,
                em_rating, authors, report_date, report_url, pdf_url,
                pdf_path, json_path, file_hash, page_count, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(info_code) DO UPDATE SET
                title=excluded.title, org=excluded.org,
                stock=excluded.stock, stock_code=excluded.stock_code,
                industry=excluded.industry, em_rating=excluded.em_rating,
                authors=excluded.authors, report_date=excluded.report_date,
                report_url=excluded.report_url, pdf_url=excluded.pdf_url,
                pdf_path=excluded.pdf_path, json_path=excluded.json_path,
                file_hash=excluded.file_hash, page_count=excluded.page_count,
                indexed_at=excluded.indexed_at""",
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
    text, err, page_count = try_extract(pdf_path)
    if text is None:
        if err:
            logger.warning(f"Corrupted PDF: {pdf_path} ({err})")
            conn.execute("UPDATE reports SET page_count=-1 WHERE info_code=?", (info_code,))
            _log_corrupted_pdf(info_code, pdf_path, err)
            conn.commit()
        return 0
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
    from .embeddings import encode, is_available
    if not is_available():
        return 0
    rows = conn.execute(
        "SELECT chunk_id, chunk_text FROM text_chunks WHERE info_code=? ORDER BY chunk_index",
        (info_code,),
    ).fetchall()
    if not rows:
        return 0
    texts = [r["chunk_text"] for r in rows]
    chunk_ids = [r["chunk_id"] for r in rows]
    # 过滤掉已有向量的块：vec0 主键冲突会直接抛异常
    placeholders = ",".join("?" * len(chunk_ids))
    have = {
        r[0]
        for r in conn.execute(
            f"SELECT chunk_id FROM {VEC_TABLE} WHERE chunk_id IN ({placeholders})", chunk_ids
        )
    }
    pairs = [(cid, t) for cid, t in zip(chunk_ids, texts) if cid not in have]
    if not pairs:
        return 0
    chunk_ids = [cid for cid, _ in pairs]
    texts = [t for _, t in pairs]
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
    return len(chunk_ids)


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
    _run_factor_phase(conn, state)


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

    batch_size = max(10, len(items) // (num_workers * 80))
    n_splits = (len(items) + batch_size - 1) // batch_size
    batches = []
    for i in range(n_splits):
        start = i * batch_size
        end = start + batch_size
        batches.append(items[start:end])

    pdf_ok = 0
    start_time = time.time()
    _last_heartbeat = start_time
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
            now = time.time()
            if now - _last_heartbeat >= 120:
                _last_heartbeat = now
                elapsed = now - start_time
                rate = pdf_ok / elapsed if elapsed > 0 else 0
                logger.info("Still working... %d/%d (%.1f%%)",
                            pdf_ok, total_pdf, pdf_ok/total_pdf*100)
            time.sleep(0.5)

    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        logger.warning("Received Ctrl+C, terminating workers and saving state...")
        pool.terminate()
        conn.commit()
        _save_state({**state, "phase": "interrupted", "total": total_pdf,
                     "done": pdf_ok, "elapsed": round(elapsed, 1),
                     "last_update": datetime.now().isoformat()})
        logger.warning(f"Index interrupted after {pdf_ok}/{total_pdf} PDFs")
        os._exit(1)
    finally:
        pool.terminate()

    conn.commit()
    logger.info(f"PDF extraction done: {pdf_ok} reports ({total_pdf - pdf_ok} skipped/corrupted)")

    _run_vector_phase(conn, state)
    _run_factor_phase(conn, state)


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
        os._exit(1)
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
        # 不能 JOIN vec0 虚拟表（逐行 KNN 查询），改为扫描后在 Python 侧比对
        vec_ids = db_module.vectorized_chunk_ids(conn)
        to_vec = sorted(
            {
                code
                for code, cid in conn.execute(
                    "SELECT info_code, chunk_id FROM text_chunks"
                )
                if cid not in vec_ids
            }
        )
        total_vec = len(to_vec)
        logger.info(f"Vector generation needed for {total_vec} reports")
        _save_state({**state, "phase": "vector_generation", "total": total_vec,
                     "done": 0, "last_update": datetime.now().isoformat()})
        vec_ok = 0
        start_time = time.time()
        for idx, code in enumerate(to_vec):
            n = index_vectors(conn, code)
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
        os._exit(1)
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
                index_factors(conn, code)
                index_factor_vectors(conn, code)
    logger.info(f"Incremental index done: {len(all_to_process)} reports")


def index_factors(conn, info_code: str) -> int:
    """从文本块中提取因子。

    重复执行会先清除该研报的旧因子，保证结果幂等。
    """
    from .factor_extractor import extract_factors_from_text

    rows = conn.execute(
        "SELECT chunk_id, chunk_text FROM text_chunks WHERE info_code=? ORDER BY chunk_index",
        (info_code,),
    ).fetchall()
    if not rows:
        return 0

    # 合并所有文本块进行因子提取
    full_text = "\n\n".join(r["chunk_text"] for r in rows)
    factors = extract_factors_from_text(full_text)

    # 清除该研报的旧因子与旧向量，避免重复累积
    old_ids = [
        r[0]
        for r in conn.execute(
            """SELECT f.factor_id FROM factors f
               JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id
               WHERE fc.info_code = ?""",
            (info_code,),
        ).fetchall()
    ]
    if old_ids:
        placeholders = ",".join("?" * len(old_ids))
        conn.execute(
            f"DELETE FROM {FACTOR_VEC_TABLE_NAME} WHERE factor_id IN ({placeholders})",
            old_ids,
        )
    conn.execute(
        "DELETE FROM factors WHERE chunk_id IN (SELECT chunk_id FROM factor_chunks WHERE info_code=?)",
        (info_code,),
    )
    conn.execute("DELETE FROM factor_chunks WHERE info_code=?", (info_code,))
    conn.commit()

    # 获取当前 chunk_id 最大值
    chunk_id_start = conn.execute(
        "SELECT COALESCE(MAX(chunk_id), 0) + 1 FROM factor_chunks"
    ).fetchone()[0]

    # 插入 factor_chunks（即使没有提取到因子也要登记，避免每次运行重复处理）
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO factor_chunks(chunk_id, info_code, chunk_text, indexed_at) VALUES (?,?,?,?)",
        (chunk_id_start, info_code, full_text[:5000], now),
    )

    # 插入 factors
    factor_data = [
        (
            chunk_id_start,
            f["factor_type"],
            f["factor_name"],
            f["value"],
            f["unit"],
            f["source_text"],
            now,
        )
        for f in factors
    ]
    if factor_data:
        conn.executemany(
            """INSERT INTO factors(chunk_id, factor_type, factor_name, value, unit, source_text, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            factor_data,
        )
    conn.commit()
    return len(factors)


def index_factor_vectors(conn, info_code: str) -> int:
    """为因子生成向量（只处理尚无向量的因子，可重复执行）"""
    from .embeddings import encode

    rows = conn.execute(
        """SELECT f.factor_id, f.source_text
           FROM factors f
           JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id
           WHERE fc.info_code = ?""",
        (info_code,),
    ).fetchall()
    if not rows:
        return 0

    # 过滤掉已有向量的因子：vec0 主键冲突会直接抛异常
    factor_ids = [r["factor_id"] for r in rows]
    placeholders = ",".join("?" * len(factor_ids))
    have = {
        r[0]
        for r in conn.execute(
            f"SELECT factor_id FROM {FACTOR_VEC_TABLE_NAME} WHERE factor_id IN ({placeholders})",
            factor_ids,
        )
    }
    rows = [r for r in rows if r["factor_id"] not in have]
    if not rows:
        return 0

    factor_ids = [r["factor_id"] for r in rows]
    texts = [r["source_text"] for r in rows]

    embeddings = encode(texts)
    if len(embeddings) == 0:
        return 0

    vec_table = FACTOR_VEC_TABLE_NAME
    vec_data = []
    for fid, emb in zip(factor_ids, embeddings):
        vec_str = "[" + ",".join(f"{v:.6f}" for v in emb) + "]"
        vec_data.append((fid, vec_str))
    conn.executemany(
        f"INSERT INTO {vec_table}(factor_id, embedding) VALUES (?,?)", vec_data
    )
    conn.commit()
    return len(rows)


_FACTOR_FAILURE_LOG_LIMIT = 5


def _log_factor_failure(failures: list, code: str, error: Exception):
    """因子阶段失败日志：前若干条详打，之后只统计总数，避免刷屏。"""
    failures.append((code, str(error)))
    if len(failures) <= _FACTOR_FAILURE_LOG_LIMIT:
        logger.warning("因子处理失败 %s: %s", code, error)
    elif len(failures) == _FACTOR_FAILURE_LOG_LIMIT + 1:
        logger.warning("因子处理失败超过 %d 条，后续仅统计总数", _FACTOR_FAILURE_LOG_LIMIT)


def _run_factor_phase(conn, state):
    """运行因子提取阶段（规则提取 + 因子向量）

    单篇研报失败只记录并继续，不得中断整个阶段。
    """
    logger.info("Starting factor extraction phase...")
    _save_state({**state, "phase": "factor_extraction", "last_update": datetime.now().isoformat()})

    try:
        to_factor = [
            r["info_code"]
            for r in conn.execute(
                """SELECT DISTINCT tc.info_code
                   FROM text_chunks tc
                   LEFT JOIN factor_chunks fc ON tc.info_code = fc.info_code
                   WHERE fc.chunk_id IS NULL"""
            ).fetchall()
        ]
    except sqlite3.OperationalError as e:
        logger.warning("因子表不可用 (%s)，跳过因子提取阶段", e)
        return

    total = len(to_factor)
    logger.info(f"Factor extraction needed for {total} reports")

    if total > 0:
        _save_state({**state, "phase": "factor_extraction", "total": total,
                     "done": 0, "last_update": datetime.now().isoformat()})

    factor_ok = 0
    failures: list = []
    start_time = time.time()
    try:
        for code in to_factor:
            try:
                n = index_factors(conn, code)
                if n > 0:
                    factor_ok += 1
                index_factor_vectors(conn, code)
            except Exception as e:
                _log_factor_failure(failures, code, e)

            if factor_ok % 20 == 0 and factor_ok > 0:
                conn.commit()
                elapsed = time.time() - start_time
                rate = factor_ok / elapsed * 60
                remaining = (total - factor_ok) / (factor_ok / elapsed) if factor_ok > 0 else 0
                progress = factor_ok / total * 100
                logger.info(f"Factors: {factor_ok}/{total} ({progress:.1f}%) [{rate:.0f}/min, est {remaining/60:.0f}min]")
                _save_state({**state, "phase": "factor_extraction", "total": total,
                             "done": factor_ok, "rate": round(rate, 1),
                             "eta_min": round(remaining / 60, 1),
                             "last_update": datetime.now().isoformat()})
        logger.info(f"Factor extraction done: {factor_ok} reports")
    except KeyboardInterrupt:
        logger.warning("Received Ctrl+C during factor extraction, exiting...")
        conn.commit()
        _save_state({**state, "phase": "interrupted", "note": "factor extraction",
                     "last_update": datetime.now().isoformat()})
        os._exit(1)
    except Exception as e:
        logger.warning(f"Factor extraction failed: {e}")

    if failures:
        logger.warning("因子处理失败 %d 篇（首条: %s）", len(failures), failures[0])

    _run_factor_vector_backfill(conn, state)

    conn.commit()
    stats = db_module.get_factor_stats(conn)
    logger.info(f"Factor stats: {stats}")


def _run_factor_vector_backfill(conn, state):
    """为缺失向量的因子补生成向量（覆盖上一轮失败的研报）"""
    from .embeddings import is_available

    if not is_available():
        logger.warning("Embedding 模型不可用，跳过因子向量生成")
        return

    try:
        # 不能 JOIN vec0 虚拟表（逐行 KNN 查询），改为扫描后在 Python 侧比对
        vec_ids = db_module.vectorized_factor_ids(conn)
        to_vec = sorted(
            {
                code
                for code, fid in conn.execute(
                    """SELECT fc.info_code, f.factor_id
                       FROM factors f
                       JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id"""
                )
                if fid not in vec_ids
            }
        )
    except sqlite3.OperationalError as e:
        logger.warning("因子向量表不可用 (%s)，跳过", e)
        return

    if not to_vec:
        logger.info("所有因子均已有向量")
        return

    logger.info(f"Factor vector backfill needed for {len(to_vec)} reports")
    failures: list = []
    for code in to_vec:
        try:
            index_factor_vectors(conn, code)
        except Exception as e:
            _log_factor_failure(failures, code, e)
    if failures:
        logger.warning("因子向量补齐失败 %d 篇", len(failures))
