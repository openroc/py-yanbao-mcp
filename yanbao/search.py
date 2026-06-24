from __future__ import annotations

import logging

from .db import get_connection
from .config import VEC_TABLE_NAME, FACTOR_VEC_TABLE_NAME

logger = logging.getLogger(__name__)

VEC_TABLE = VEC_TABLE_NAME
SEARCH_LIMIT = 30


def _has_cjk(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            return True
    return False


def _reports_base(conn):
    return """SELECT r.info_code, r.title, r.org, r.stock, r.stock_code,
                     r.industry, r.em_rating, r.report_date, r.page_count,
                     r.pdf_path, r.json_path,
                     (SELECT COUNT(*) FROM text_chunks WHERE info_code=r.info_code) as chunk_count
              FROM reports r"""


def search_metadata(
    conn,
    query: str | None = None,
    stock_code: str | None = None,
    stock_name: str | None = None,
    industry: str | None = None,
    org: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = SEARCH_LIMIT,
) -> list[dict]:
    conditions = []
    params = []

    if query:
        if _has_cjk(query):
            chars = " ".join(query)
            like_q = f"%{query}%"
            conditions.append(
                """((r.rowid IN (
                       SELECT rowid FROM reports_fts WHERE reports_fts MATCH ?
                   )) OR r.title LIKE ?)"""
            )
            params.append(chars)
            params.append(like_q)
        else:
            conditions.append(
                """(r.rowid IN (
                       SELECT rowid FROM reports_fts WHERE reports_fts MATCH ?
                   ))"""
            )
            fts_query = " OR ".join(f'"{w}"' for w in query.split() if w)
            params.append(fts_query)

    if stock_code:
        conditions.append("r.stock_code = ?")
        params.append(stock_code)

    if stock_name:
        conditions.append("r.stock LIKE ?")
        params.append(f"%{stock_name}%")

    if industry:
        conditions.append("r.industry LIKE ?")
        params.append(f"%{industry}%")

    if org:
        conditions.append("r.org LIKE ?")
        params.append(f"%{org}%")

    if date_from:
        conditions.append("r.report_date >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("r.report_date <= ?")
        params.append(date_to)

    sql = _reports_base(conn)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY r.report_date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def semantic_search(
    conn, query: str, top_k: int = 10
) -> list[dict]:
    from .embeddings import encode
    query_vec = encode([query])[0]
    vec_str = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"

    sql = f"""
        SELECT cv.chunk_id, cv.distance, tc.info_code, tc.chunk_index, tc.chunk_text,
               r.title, r.stock, r.stock_code, r.industry, r.org, r.report_date, r.em_rating
        FROM {VEC_TABLE} cv
        JOIN text_chunks tc ON cv.chunk_id = tc.chunk_id
        JOIN reports r ON tc.info_code = r.info_code
        WHERE cv.embedding MATCH ? AND k = ?
        ORDER BY cv.distance
    """
    rows = conn.execute(sql, (vec_str, top_k)).fetchall()
    seen_codes = set()
    results = []
    for r in rows:
        code = r["info_code"]
        seen_codes.add(code)
        results.append({
            "info_code": code,
            "title": r["title"],
            "stock": r["stock"],
            "stock_code": r["stock_code"],
            "industry": r["industry"],
            "org": r["org"],
            "report_date": r["report_date"],
            "em_rating": r["em_rating"],
            "distance": round(r["distance"], 4),
            "chunk_index": r["chunk_index"],
            "chunk_text_snippet": r["chunk_text"][:300],
        })
    return results


def get_report(conn, info_code: str) -> dict | None:
    row = conn.execute(
        _reports_base(conn) + " WHERE r.info_code = ?", (info_code,)
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    chunks = conn.execute(
        "SELECT chunk_index, chunk_text, token_count FROM text_chunks WHERE info_code=? ORDER BY chunk_index",
        (info_code,),
    ).fetchall()
    result["chunks"] = [dict(c) for c in chunks]
    return result


def list_by_stock(conn, stock_code: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        _reports_base(conn)
        + " WHERE r.stock_code = ? ORDER BY r.report_date DESC LIMIT ?",
        (stock_code, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_by_industry(conn, industry: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    rows = conn.execute(
        _reports_base(conn)
        + " WHERE r.industry LIKE ? ORDER BY r.report_date DESC LIMIT ?",
        (f"%{industry}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def search_hybrid(
    conn, query: str, top_k: int = 10
) -> list[dict]:
    fts_results = search_metadata(conn, query=query, limit=top_k)
    fts_codes = {r["info_code"] for r in fts_results}

    semantic_results = semantic_search(conn, query, top_k)
    seen = set()
    merged = []
    for r in semantic_results:
        code = r["info_code"]
        if code not in fts_codes:
            seen.add(code)
            merged.append(r)
    for r in fts_results:
        code = r["info_code"]
        merged.append({
            "info_code": code,
            "title": r["title"],
            "stock": r["stock"],
            "stock_code": r["stock_code"],
            "industry": r["industry"],
            "org": r["org"],
            "report_date": r["report_date"],
            "em_rating": r["em_rating"],
            "distance": None,
        })
    return merged[:top_k]


def search_factors(
    conn,
    factor_type: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    unit: str | None = None,
    info_code: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    按因子类型和数值范围搜索因子

    Args:
        conn: 数据库连接
        factor_type: 因子类型 (产能/订单/报价/增速/政策/成本/库存/销量)
        min_value: 最小值
        max_value: 最大值
        unit: 单位
        info_code: 限定研报
        limit: 返回数量
    """
    conditions = []
    params = []

    if factor_type:
        conditions.append("f.factor_type = ?")
        params.append(factor_type)

    if min_value is not None:
        conditions.append("f.value >= ?")
        params.append(min_value)

    if max_value is not None:
        conditions.append("f.value <= ?")
        params.append(max_value)

    if unit:
        conditions.append("f.unit = ?")
        params.append(unit)

    if info_code:
        conditions.append("fc.info_code = ?")
        params.append(info_code)

    sql = """
        SELECT f.factor_id, f.factor_type, f.factor_name, f.value, f.unit,
               f.source_text, fc.info_code, r.title, r.stock, r.stock_code,
               r.org, r.report_date
        FROM factors f
        JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id
        JOIN reports r ON fc.info_code = r.info_code
    """
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY f.value DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def semantic_factor_search(
    conn, query: str, factor_type: str | None = None, top_k: int = 10
) -> list[dict]:
    """
    语义搜索因子（向量相似度）

    Args:
        conn: 数据库连接
        query: 自然语言查询
        factor_type: 可选的因子类型过滤
        top_k: 返回数量
    """
    from .embeddings import encode

    query_vec = encode([query])[0]
    vec_str = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"

    factor_vec_table = FACTOR_VEC_TABLE_NAME

    if factor_type:
        sql = f"""
            SELECT fv.factor_id, fv.distance, f.factor_type, f.factor_name,
                   f.value, f.unit, f.source_text, fc.info_code,
                   r.title, r.stock, r.stock_code, r.org, r.report_date
            FROM {factor_vec_table} fv
            JOIN factors f ON fv.factor_id = f.factor_id
            JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id
            JOIN reports r ON fc.info_code = r.info_code
            WHERE fv.embedding MATCH ? AND k = ? AND f.factor_type = ?
            ORDER BY fv.distance
        """
        rows = conn.execute(sql, (vec_str, top_k, factor_type)).fetchall()
    else:
        sql = f"""
            SELECT fv.factor_id, fv.distance, f.factor_type, f.factor_name,
                   f.value, f.unit, f.source_text, fc.info_code,
                   r.title, r.stock, r.stock_code, r.org, r.report_date
            FROM {factor_vec_table} fv
            JOIN factors f ON fv.factor_id = f.factor_id
            JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id
            JOIN reports r ON fc.info_code = r.info_code
            WHERE fv.embedding MATCH ? AND k = ?
            ORDER BY fv.distance
        """
        rows = conn.execute(sql, (vec_str, top_k)).fetchall()

    results = []
    for r in rows:
        results.append({
            "factor_id": r["factor_id"],
            "factor_type": r["factor_type"],
            "factor_name": r["factor_name"],
            "value": r["value"],
            "unit": r["unit"],
            "source_text": r["source_text"],
            "info_code": r["info_code"],
            "title": r["title"],
            "stock": r["stock"],
            "stock_code": r["stock_code"],
            "org": r["org"],
            "report_date": r["report_date"],
            "distance": round(r["distance"], 4),
        })
    return results


def get_factors_by_report(conn, info_code: str) -> list[dict]:
    """获取某研报的所有因子"""
    rows = conn.execute("""
        SELECT f.factor_id, f.factor_type, f.factor_name, f.value, f.unit,
               f.source_text, fc.chunk_text
        FROM factors f
        JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id
        WHERE fc.info_code = ?
        ORDER BY f.factor_type, f.value DESC
    """, (info_code,)).fetchall()
    return [dict(r) for r in rows]
