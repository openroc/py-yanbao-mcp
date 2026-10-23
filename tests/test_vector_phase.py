"""文本向量管线测试 — 幂等性与 vec0 慢路径规避。"""

import sqlite3
import sys
from pathlib import Path

import sqlite_vec

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yanbao import db, indexer
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


# --- 幂等性 ---------------------------------------------------------------

def test_index_vectors_only_inserts_missing(monkeypatch):
    """已向量化的块不得重复写入 vec0（主键冲突）。"""
    conn = _fresh_conn()
    init_db(conn)
    _seed_report(conn, "R0")

    import yanbao.embeddings as embeddings
    from yanbao.config import EMBEDDING_DIM
    monkeypatch.setattr(
        embeddings, "encode", lambda texts: [[0.0] * EMBEDDING_DIM for _ in texts]
    )

    n1 = indexer.index_vectors(conn, "R0")
    n2 = indexer.index_vectors(conn, "R0")
    rows = conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
    assert n1 == 1
    assert n2 == 0
    assert rows == n1
    conn.close()


def test_index_vectors_skips_partially_vectorized_report(monkeypatch):
    """部分块已有向量时，只补缺失的那些，不得因主键冲突而中断。"""
    conn = _fresh_conn()
    init_db(conn)
    _seed_report(conn, "R0")
    conn.execute(
        "INSERT INTO text_chunks(info_code, chunk_index, chunk_text, token_count) VALUES (?,?,?,?)",
        ("R0", 1, "第二个块的内容", 7),
    )
    conn.commit()

    import yanbao.embeddings as embeddings
    from yanbao.config import EMBEDDING_DIM
    monkeypatch.setattr(
        embeddings, "encode", lambda texts: [[0.0] * EMBEDDING_DIM for _ in texts]
    )

    assert indexer.index_vectors(conn, "R0") == 2
    # 人为删掉第二个块对应的向量
    conn.execute(
        "DELETE FROM chunk_vectors WHERE chunk_id = "
        "(SELECT chunk_id FROM text_chunks WHERE info_code='R0' AND chunk_index=1)"
    )
    conn.commit()
    assert indexer.index_vectors(conn, "R0") == 1
    rows = conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
    assert rows == 2
    conn.close()


# --- vec0 慢路径 ----------------------------------------------------------

def test_vectorized_chunk_ids_matches_database(conn=None):
    """db.vectorized_chunk_ids 用扫描而非 JOIN，结果必须与表内容一致。"""
    from yanbao.config import EMBEDDING_DIM
    conn = _fresh_conn()
    init_db(conn)
    _seed_report(conn, "R0")
    vec = "[" + ",".join("0.0" for _ in range(EMBEDDING_DIM)) + "]"
    conn.execute(
        "INSERT INTO chunk_vectors(chunk_id, embedding) VALUES (1, ?)", (vec,)
    )
    conn.commit()
    ids = db.vectorized_chunk_ids(conn)
    assert ids == {1}
    conn.close()


def test_vector_phase_skips_fully_vectorized_reports(monkeypatch, tmp_path):
    """全部块都有向量的研报不应进入向量生成队列。"""
    # 阶段函数会写状态文件，测试里必须隔离，否则覆盖生产状态
    monkeypatch.setattr(indexer, "INDEX_STATE_PATH", tmp_path / "state.json")
    conn = _fresh_conn()
    init_db(conn)
    _seed_report(conn, "R0")

    import yanbao.embeddings as embeddings
    from yanbao.config import EMBEDDING_DIM
    monkeypatch.setattr(
        embeddings, "encode", lambda texts: [[0.0] * EMBEDDING_DIM for _ in texts]
    )
    assert indexer.index_vectors(conn, "R0") == 1

    called = []
    monkeypatch.setattr(
        indexer, "index_vectors", lambda c, code: called.append(code) or 1
    )
    indexer._run_vector_phase(conn, {"phase": "x"})
    assert called == []
    conn.close()
