"""LLM 客户端模块 — 对接 llama-server (OpenAI 兼容 API)"""

import logging
from typing import Any

import httpx

from . import db as db_module
from . import search as search_module
from .config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL, LLM_TIMEOUT

logger = logging.getLogger("yanbao.llm")

_RAG_SYSTEM_PROMPT = """你是一个专业研报分析师。基于以下研报片段回答问题。
如果片段信息不足，请如实说明。引用来源时标注[1][2]等编号。

研报片段：
{context}

请用中文回答，并在关键结论后标注来源编号。"""

_SUMMARY_PROMPT = """请用中文对以下研报内容进行简洁摘要（200字以内），
涵盖：核心观点、评级、关键数据。

研报内容：
{text}

摘要："""

_SYNTHESIS_PROMPT = """你是一个专业研报分析师。基于以下多份研报的片段，进行综合分析。
分析要点：
1. 各券商的核心观点
2. 分歧与共识
3. 关键数据支撑

研报片段：
{context}

问题：{query}

请用中文分点回答。"""


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        api_key: str | None = None,
    ):
        self.base_url = (base_url or LLM_BASE_URL).rstrip("/")
        self.model = model or LLM_MODEL or None
        self.timeout = timeout or LLM_TIMEOUT
        self.api_key = api_key if api_key is not None else (LLM_API_KEY or None)
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.Client(
                base_url=self.base_url, timeout=self.timeout, headers=headers
            )
        return self._client

    def chat(self, messages: list[dict], **kwargs) -> str:
        body = {"messages": messages}
        if self.model:
            body["model"] = self.model
        body.update(kwargs)
        resp = self.client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None


def _build_context(results: list[dict]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[{i}] (报告日期: {r.get('report_date','')}, "
            f"券商: {r.get('org','')}, "
            f"标的: {r.get('stock','')}({r.get('stock_code','')}))\n"
            f"{r.get('chunk_text_snippet','')}"
        )
    return "\n\n".join(parts)


def _check_enabled():
    if not LLM_ENABLED:
        raise RuntimeError(
            "LLM 功能未启用。设置环境变量 YANBAO_LLM_ENABLED=true "
            "或检查 .env 配置"
        )


def _sort_by_date(results: list[dict]) -> list[dict]:
    """按报告日期降序排列（最新优先），同日期保序"""
    return sorted(results, key=lambda r: r.get("report_date", ""), reverse=True)


def _hybrid_search(conn, query: str, top_k: int = 20) -> list[dict]:
    """语义搜索 + 最近报告混合检索, 按日期排序."""
    semantic_results = search_module.semantic_search(conn, query, top_k=top_k)

    seen = set(r["info_code"] for r in semantic_results)

    # Fetch recent reports for the same stocks
    stocks = set(r.get("stock_code") for r in semantic_results if r.get("stock_code"))
    for stock_code in stocks:
        recent = search_module.list_by_stock(conn, stock_code, limit=5)
        for r in recent:
            if r["info_code"] not in seen:
                seen.add(r["info_code"])
                chunk = conn.execute(
                    "SELECT chunk_text FROM text_chunks WHERE info_code=? ORDER BY chunk_index LIMIT 1",
                    (r["info_code"],),
                ).fetchone()
                semantic_results.append({
                    "info_code": r["info_code"],
                    "title": r["title"],
                    "stock": r["stock"],
                    "stock_code": r["stock_code"],
                    "industry": r["industry"],
                    "org": r["org"],
                    "report_date": r["report_date"],
                    "em_rating": r["em_rating"],
                    "distance": None,
                    "chunk_index": 0,
                    "chunk_text_snippet": chunk["chunk_text"][:300] if chunk else "(无文本)",
                })

    return _sort_by_date(semantic_results)[:top_k]


def rag_answer(query: str, top_k: int = 15) -> dict[str, Any]:
    _check_enabled()
    conn = db_module.get_connection()
    try:
        results = _hybrid_search(conn, query, top_k=top_k)
        if not results:
            return {"answer": "未找到相关研报内容。", "citations": []}
        context = _build_context(results)
        system_prompt = _RAG_SYSTEM_PROMPT.format(context=context)
        llm = LLMClient()
        try:
            answer = llm.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ])
        finally:
            llm.close()
        return {"answer": answer, "citations": results}
    finally:
        conn.close()


def summarize(info_code: str) -> str:
    _check_enabled()
    conn = db_module.get_connection()
    try:
        report = search_module.get_report(conn, info_code)
        if report is None:
            return "未找到该研报。"
        chunks = report.get("chunks", [])
        if not chunks:
            return "研报无文本内容。"
        text = "\n\n".join(
            f"[第{c['chunk_index']}段] {c['chunk_text']}" for c in chunks
        )
        prompt = _SUMMARY_PROMPT.format(text=text[:8000])
        llm = LLMClient()
        try:
            result = llm.chat([{"role": "user", "content": prompt}])
        finally:
            llm.close()
        return result
    finally:
        conn.close()


def synthesize(query: str, top_k: int = 20) -> dict[str, Any]:
    _check_enabled()
    conn = db_module.get_connection()
    try:
        results = _hybrid_search(conn, query, top_k=top_k)
        if not results:
            return {"answer": "未找到相关研报内容。", "citations": []}
        context = _build_context(results)
        prompt = _SYNTHESIS_PROMPT.format(context=context, query=query)
        llm = LLMClient()
        try:
            answer = llm.chat([{"role": "user", "content": prompt}])
        finally:
            llm.close()
        return {"answer": answer, "citations": results}
    finally:
        conn.close()
