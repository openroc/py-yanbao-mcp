"""重建文本块与向量（不重新解析 PDF）。

背景：旧版 chunker 没有长度上限，线上 94% 的文本块远超 embedding 模型的
512 token 上限，向量是在被截断的文本上算出来的，语义检索结果不可信。

旧 chunker 只在段落边界切块，所以把某篇研报的所有旧块按 chunk_index 用
"\n\n" 拼回去，就是当初从 PDF 抽出的原文（段落顺序完整、无内容丢失）。
本脚本据此用当前 chunk_text 重新切分，并重新生成 chunk 向量。

用法:
    python rechunk_and_reindex.py              # 全量
    python rechunk_and_reindex.py --limit 200  # 只处理前 200 篇
    python rechunk_and_reindex.py --no-vectors # 只重建文本块

可重复执行：已重建的研报（所有块 <= CHUNK_SIZE 且向量数 = 块数）会被跳过。
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yanbao import db
from yanbao.config import CHUNK_SIZE, VEC_TABLE_NAME
from yanbao.extractor import chunk_text

logger = logging.getLogger("rechunk")

BATCH_REPORTS = 100
BATCH_VECTORS = 256


def _done_reports(conn) -> set[str]:
    """返回已重建（块长度合规且向量齐全）的研报集合。

    注意：不能把普通表与 vec0 虚拟表 JOIN —— sqlite-vec 的向量匹配是逐行
    KNN 查询，68k 行 JOIN 会跑几十分钟。这里改为一次全表扫描后在 Python
    侧比对。
    """
    vec_ids = {r[0] for r in conn.execute(f"SELECT chunk_id FROM {VEC_TABLE_NAME}")}
    per_report: dict[str, list] = {}
    for code, cid, length in conn.execute(
        "SELECT info_code, chunk_id, length(chunk_text) FROM text_chunks"
    ):
        entry = per_report.setdefault(code, [0, 0, True])
        entry[0] += 1
        entry[1] = max(entry[1], length)
        if cid not in vec_ids:
            entry[2] = False
    return {
        code
        for code, (_n, max_len, all_vec) in per_report.items()
        if max_len <= CHUNK_SIZE and all_vec
    }


def _report_texts(conn, codes: list[str]) -> dict[str, str]:
    """把每篇研报的旧块拼回全文。"""
    texts = {}
    for code in codes:
        rows = conn.execute(
            "SELECT chunk_text FROM text_chunks WHERE info_code=? ORDER BY chunk_index",
            (code,),
        ).fetchall()
        texts[code] = "\n\n".join(r[0] for r in rows)
    return texts


def _replace_chunks(conn, code: str, chunks: list[tuple[str, int]], next_id: int) -> list[int]:
    """删除该研报的旧块与旧向量，写入新块。返回新 chunk_id 列表。"""
    old_ids = [
        r[0]
        for r in conn.execute(
            "SELECT chunk_id FROM text_chunks WHERE info_code=?", (code,)
        ).fetchall()
    ]
    if old_ids:
        # vec0 的 DELETE 按主键等值逐条执行，IN (...) 不走索引且无法批量
        conn.executemany(
            f"DELETE FROM {VEC_TABLE_NAME} WHERE chunk_id = ?",
            [(cid,) for cid in old_ids],
        )
    conn.execute("DELETE FROM text_chunks WHERE info_code=?", (code,))

    ids = []
    rows = []
    for i, (c_text, c_len) in enumerate(chunks):
        ids.append(next_id)
        rows.append((next_id, code, i, c_text, c_len))
        next_id += 1
    if rows:
        conn.executemany(
            """INSERT INTO text_chunks(chunk_id, info_code, chunk_index, chunk_text, token_count)
               VALUES (?,?,?,?,?)""",
            rows,
        )
    return ids, next_id


def _embed_and_store(conn, items: list[tuple[int, str]], no_vectors: bool) -> int:
    """对 (chunk_id, text) 批量生成向量并写入 vec0。"""
    if no_vectors or not items:
        return 0
    from yanbao.embeddings import encode, is_available

    if not is_available():
        logger.warning("embedding 模型不可用，跳过向量生成")
        return 0

    written = 0
    for start in range(0, len(items), BATCH_VECTORS):
        batch = items[start : start + BATCH_VECTORS]
        vectors = encode([t for _, t in batch])
        if len(vectors) == 0:
            break
        vec_data = [
            (cid, "[" + ",".join(f"{v:.6f}" for v in emb) + "]")
            for (cid, _), emb in zip(batch, vectors)
        ]
        conn.executemany(
            f"INSERT INTO {VEC_TABLE_NAME}(chunk_id, embedding) VALUES (?,?)", vec_data
        )
        written += len(vec_data)
        if written % (BATCH_VECTORS * 10) == 0:
            conn.commit()
    conn.commit()
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少篇（0=不限）")
    parser.add_argument("--no-vectors", action="store_true", help="只重建文本块，不生成向量")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    conn = db.get_connection()
    db.init_db(conn)

    done = _done_reports(conn)
    all_codes = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT info_code FROM text_chunks ORDER BY info_code"
        ).fetchall()
    ]
    todo = [c for c in all_codes if c not in done]
    if args.limit:
        todo = todo[: args.limit]

    logger.info(
        "共 %d 篇研报，已完成 %d 篇，待处理 %d 篇",
        len(all_codes),
        len(done),
        len(todo),
    )
    if not todo:
        logger.info("没有需要重建的研报")
        conn.close()
        return

    next_chunk_id = conn.execute(
        "SELECT COALESCE(MAX(chunk_id), 0) + 1 FROM text_chunks"
    ).fetchone()[0]

    started = time.time()
    total_new_chunks = 0
    total_vectors = 0

    for offset in range(0, len(todo), BATCH_REPORTS):
        batch = todo[offset : offset + BATCH_REPORTS]
        texts = _report_texts(conn, batch)

        pending: list[tuple[int, str]] = []
        for code in batch:
            new_chunks = chunk_text(texts.get(code, ""))
            if not new_chunks:
                _replace_chunks(conn, code, [], next_chunk_id)
                continue
            ids, next_chunk_id = _replace_chunks(conn, code, new_chunks, next_chunk_id)
            total_new_chunks += len(ids)
            pending.extend(zip(ids, [c[0] for c in new_chunks]))
        conn.commit()

        total_vectors += _embed_and_store(conn, pending, args.no_vectors)

        done_n = offset + len(batch)
        elapsed = time.time() - started
        rate = done_n / elapsed if elapsed else 0
        remaining = (len(todo) - done_n) / rate if rate else 0
        logger.info(
            "%d/%d 篇 | 新块 %d | 向量 %d | %.1f 篇/分 | 预计剩余 %.0f 分钟",
            done_n,
            len(todo),
            total_new_chunks,
            total_vectors,
            rate * 60,
            remaining / 60,
        )

    logger.info(
        "完成：重建 %d 个文本块，生成 %d 条向量，用时 %.1f 分钟",
        total_new_chunks,
        total_vectors,
        (time.time() - started) / 60,
    )
    conn.close()


if __name__ == "__main__":
    main()
