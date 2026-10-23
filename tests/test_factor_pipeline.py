"""因子管线测试 — 表初始化、阶段容错、增量路径。"""

import sqlite3
import sys
from pathlib import Path

import sqlite_vec

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yanbao import indexer
from yanbao.db import init_db


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_report(conn, info_code: str, text: str = "公司产能 100 万吨，同比增长 20%"):
    conn.execute(
        """INSERT OR REPLACE INTO reports
           (info_code, title, org, stock, stock_code, industry, em_rating,
            authors, report_date, report_url, pdf_url, pdf_path, json_path,
            file_hash, page_count, indexed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (info_code, f"标题{info_code}", "测试券商", "测试股", "600000", "半导体",
         "买入", "[]", "2026-01-01", "", "", "x.pdf", "x.json", "h", 1, "now"),
    )
    conn.execute(
        "INSERT INTO text_chunks(info_code, chunk_index, chunk_text, token_count) VALUES (?,?,?,?)",
        (info_code, 0, text, len(text)),
    )
    conn.commit()


# --- #2 表初始化 ---------------------------------------------------------

def test_init_db_creates_factor_tables():
    """init_db 必须一次性建好全部表（含因子表），否则索引流程会崩。"""
    conn = _fresh_conn()
    init_db(conn)
    names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "factor_chunks" in names
    assert "factors" in names
    assert "factor_vectors" in names
    conn.close()


def test_init_db_is_idempotent():
    conn = _fresh_conn()
    init_db(conn)
    init_db(conn)
    conn.close()


# --- #3 阶段容错 ---------------------------------------------------------

def test_factor_phase_survives_single_report_failure(monkeypatch):
    """单篇研报因子处理失败，不得中断整个因子阶段。"""
    conn = _fresh_conn()
    init_db(conn)
    for i in range(3):
        _seed_report(conn, f"R{i}")

    def fake_vectors(conn, info_code):
        if info_code == "R1":
            raise RuntimeError("boom")
        return 1

    monkeypatch.setattr(indexer, "index_factor_vectors", fake_vectors)
    indexer._run_factor_phase(conn, {"phase": "x"})

    done = {r[0] for r in conn.execute("SELECT DISTINCT info_code FROM factor_chunks")}
    assert done == {"R0", "R1", "R2"}
    conn.close()


def test_factor_phase_skips_when_tables_missing(monkeypatch):
    """因子表不存在时应降级跳过，而不是抛异常终止索引。"""
    conn = _fresh_conn()
    conn.executescript(
        "CREATE TABLE reports (info_code TEXT PRIMARY KEY);"
        "CREATE TABLE text_chunks (chunk_id INTEGER PRIMARY KEY, info_code TEXT, chunk_text TEXT);"
    )
    indexer._run_factor_phase(conn, {"phase": "x"})
    conn.close()


def test_index_factors_is_idempotent():
    """重复提取同一研报，因子不应翻倍累积。"""
    conn = _fresh_conn()
    init_db(conn)
    _seed_report(conn, "R0")

    first = indexer.index_factors(conn, "R0")
    second = indexer.index_factors(conn, "R0")

    total = conn.execute("SELECT COUNT(*) FROM factors").fetchone()[0]
    assert first > 0
    assert second == first
    assert total == first
    conn.close()


def test_index_factor_vectors_only_inserts_missing(monkeypatch):
    """已向量化的因子不得重复写入 vec0（主键冲突）。"""
    conn = _fresh_conn()
    init_db(conn)
    _seed_report(conn, "R0")
    indexer.index_factors(conn, "R0")

    inserted = []
    import yanbao.embeddings as embeddings
    from yanbao.config import EMBEDDING_DIM
    monkeypatch.setattr(
        embeddings, "encode", lambda texts: [[0.0] * EMBEDDING_DIM for _ in texts]
    )

    n1 = indexer.index_factor_vectors(conn, "R0")
    n2 = indexer.index_factor_vectors(conn, "R0")
    rows = conn.execute("SELECT COUNT(*) FROM factor_vectors").fetchone()[0]
    assert n1 > 0
    assert n2 == 0
    assert rows == n1
    conn.close()


# --- #3 增量路径 ---------------------------------------------------------

def test_run_incremental_generates_factor_vectors(monkeypatch):
    """增量索引也必须生成因子向量。"""
    conn = _fresh_conn()
    init_db(conn)

    record = {
        "info_code": "R0", "title": "t", "org": "o", "stock": "s", "stock_code": "c",
        "industry": "i", "em_rating": "r", "authors": "[]", "report_date": "2026-01-01",
        "report_url": "", "pdf_url": "", "pdf_path": "x.pdf", "json_path": "x.json",
        "file_hash": "h",
    }
    monkeypatch.setattr(indexer, "scan_all", lambda: [record])
    monkeypatch.setattr(indexer, "find_new_and_changed", lambda c, r: (r, []))

    def fake_pdf_content(conn, info_code, pdf_path):
        _seed_report(conn, info_code)
        return 1

    monkeypatch.setattr(indexer, "index_pdf_content", fake_pdf_content)

    called = []
    monkeypatch.setattr(
        indexer, "index_factor_vectors", lambda c, code: called.append(code) or 1
    )

    indexer.run_incremental(conn)
    assert called == ["R0"]
    conn.close()
