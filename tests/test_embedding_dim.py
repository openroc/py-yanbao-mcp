"""向量维度一致性测试。

EMBEDDING_DIM 必须与 embedding 模型的实际输出维度一致，否则：
- 新建库的 vec0 表维度错误 → 每一条向量插入都报 Dimension mismatch
- 线上旧库维度正确 → 问题被掩盖，直到重建索引才暴露
"""

import sqlite3
import sys
from pathlib import Path

import pytest
import sqlite_vec

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yanbao.config import EMBEDDING_DIM, EMBEDDING_MODEL, VEC_TABLE_NAME
from yanbao.db import init_db


def _model_hidden_size() -> int | None:
    """从本地 HF 缓存读模型 config.json 的 hidden_size；没有缓存则返回 None。"""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None
    try:
        path = hf_hub_download(
            EMBEDDING_MODEL, "config.json", local_files_only=True
        )
    except Exception:
        return None
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("hidden_size")


def test_embedding_dim_matches_model():
    """配置维度必须等于模型输出维度。"""
    hidden = _model_hidden_size()
    if hidden is None:
        pytest.skip("本地无 embedding 模型缓存，跳过")
    assert EMBEDDING_DIM == hidden, (
        f"EMBEDDING_DIM={EMBEDDING_DIM} 与模型 {EMBEDDING_MODEL} 的 "
        f"hidden_size={hidden} 不符"
    )


def test_fresh_db_vector_table_uses_configured_dim():
    """新建库的向量表维度必须与配置一致。"""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    init_db(conn)
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name=?", (VEC_TABLE_NAME,)
    ).fetchone()[0]
    assert f"float[{EMBEDDING_DIM}]" in ddl
    conn.close()
