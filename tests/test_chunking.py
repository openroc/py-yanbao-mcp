"""分块逻辑测试 — 保证 chunk 不超过 embedding 模型的 token 上限。

bge-small-zh-v1.5 位置编码上限为 512 token；中文约 1 字 ≈ 1 token，
因此 CHUNK_SIZE(字符) 即为安全上限。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yanbao.config import CHUNK_SIZE
from yanbao.extractor import chunk_text


def test_long_single_paragraph_is_split():
    """超长单段落必须被切开，不能原样塞进一个 chunk。"""
    text = "研" * 3000
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c[0]) <= CHUNK_SIZE for c in chunks)


def test_no_chunk_exceeds_chunk_size():
    """任何输入下 chunk 长度都不得超过 CHUNK_SIZE。"""
    text = "\n\n".join(["研报正文内容测试" * 200] * 5)
    chunks = chunk_text(text)
    assert chunks
    assert all(len(c[0]) <= CHUNK_SIZE for c in chunks)


def test_realistic_pdf_text_is_bounded():
    """模拟 pypdf 抽取结果：段落极长且长度不一。"""
    text = "\n".join(f"第{i}行内容" * 60 for i in range(400))
    chunks = chunk_text(text)
    assert chunks
    assert all(len(c[0]) <= CHUNK_SIZE for c in chunks)


def test_content_is_preserved():
    """切分不得丢失原文。"""
    text = "\n\n".join(f"段落{i}内容" + "填充" * 200 for i in range(20))
    chunks = chunk_text(text)
    joined = "".join(c[0] for c in chunks)
    for i in range(20):
        assert f"段落{i}内容" in joined


def test_short_paragraphs_are_grouped():
    """短段落仍然合并成单个 chunk，不因新增切分而碎片化。"""
    chunks = chunk_text("短段一\n\n短段二")
    assert len(chunks) == 1
    assert chunks[0][0] == "短段一\n\n短段二"


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []


def test_chunk_length_metadata_matches_text():
    """第二个返回值是内容长度，应与 chunk 文本长度一致。"""
    chunks = chunk_text("短段一\n\n短段二")
    assert chunks[0][1] == len(chunks[0][0])
