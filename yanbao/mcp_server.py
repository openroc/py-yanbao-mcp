from __future__ import annotations

import asyncio
import logging
import sys

import anyio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.shared.message import SessionMessage

from . import db as db_module
from . import llm as llm_module
from . import search as search_module

logger = logging.getLogger("yanbao-mcp")
server = Server("yanbao-mcp")


def _get_conn():
    conn = db_module.get_connection()
    return conn


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_reports",
            description="按条件搜索研报（元数据+关键词）",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "stock_code": {"type": "string", "description": "股票代码，如 600988"},
                    "stock_name": {"type": "string", "description": "股票名称"},
                    "industry": {"type": "string", "description": "行业，如 新能源、半导体"},
                    "org": {"type": "string", "description": "券商名称"},
                    "date_from": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                    "limit": {"type": "integer", "description": "返回数量上限", "default": 20},
                },
            },
        ),
        types.Tool(
            name="semantic_search",
            description="语义搜索研报（自然语言查询）",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言查询"},
                    "top_k": {"type": "integer", "description": "返回结果数", "default": 10},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_report",
            description="获取单篇研报详情",
            inputSchema={
                "type": "object",
                "properties": {
                    "info_code": {"type": "string", "description": "研报infoCode"},
                },
                "required": ["info_code"],
            },
        ),
        types.Tool(
            name="list_by_stock",
            description="获取某只股票的全部研报",
            inputSchema={
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "股票代码"},
                    "limit": {"type": "integer", "description": "返回数量上限", "default": 50},
                },
                "required": ["stock_code"],
            },
        ),
        types.Tool(
            name="list_by_industry",
            description="获取某个行业的研报",
            inputSchema={
                "type": "object",
                "properties": {
                    "industry": {"type": "string", "description": "行业名称"},
                    "limit": {"type": "integer", "description": "返回数量上限", "default": 20},
                },
                "required": ["industry"],
            },
        ),
        types.Tool(
            name="get_stats",
            description="获取知识库统计信息",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="ask",
            description="基于研报知识库问答（自然语言提问，LLM 生成回答）",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言问题"},
                    "top_k": {
                        "type": "integer",
                        "description": "检索相关片段数",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    conn = _get_conn()
    try:
        if name == "search_reports":
            args = arguments or {}
            results = search_module.search_metadata(
                conn,
                query=args.get("query"),
                stock_code=args.get("stock_code"),
                stock_name=args.get("stock_name"),
                industry=args.get("industry"),
                org=args.get("org"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                limit=args.get("limit", 20),
            )
            return [types.TextContent(type="text", text=_fmt(results))]

        elif name == "semantic_search":
            args = arguments or {}
            results = search_module.semantic_search(
                conn, query=args["query"], top_k=args.get("top_k", 10)
            )
            return [types.TextContent(type="text", text=_fmt_semantic(results))]

        elif name == "get_report":
            info_code = arguments.get("info_code", "")
            result = search_module.get_report(conn, info_code)
            if result is None:
                return [types.TextContent(type="text", text="未找到该研报")]
            return [types.TextContent(type="text", text=_fmt_report(result))]

        elif name == "list_by_stock":
            args = arguments or {}
            results = search_module.list_by_stock(
                conn, stock_code=args["stock_code"], limit=args.get("limit", 50)
            )
            return [types.TextContent(type="text", text=_fmt(results))]

        elif name == "list_by_industry":
            args = arguments or {}
            results = search_module.list_by_industry(
                conn, industry=args["industry"], limit=args.get("limit", 20)
            )
            return [types.TextContent(type="text", text=_fmt(results))]

        elif name == "get_stats":
            stats = db_module.get_stats(conn)
            text = (
                f"报告总数: {stats['total_reports']}\n"
                f"文本块数: {stats['total_chunks']}\n"
                f"覆盖标的: {stats['unique_stocks']}\n"
                f"覆盖行业: {stats['unique_industries']}\n"
                f"覆盖券商: {stats['unique_orgs']}\n"
                f"日期范围: {stats['date_range'][0]} ~ {stats['date_range'][1]}"
            )
            return [types.TextContent(type="text", text=text)]

        elif name == "ask":
            args = arguments or {}
            query = args.get("query", "")
            top_k = args.get("top_k", 8)
            try:
                result = llm_module.rag_answer(query, top_k=top_k)
            except RuntimeError as e:
                return [types.TextContent(type="text", text=f"LLM 未启用: {e}")]
            except Exception as e:
                return [types.TextContent(type="text", text=f"LLM 调用失败: {e}")]
            text = result["answer"]
            if result.get("citations"):
                text += "\n\n--- 参考来源 ---\n"
                for r in result["citations"]:
                    text += (
                        f"[{r.get('report_date','')}] {r.get('org','')} | "
                        f"{r.get('stock','')}({r.get('stock_code','')}) | "
                        f"{r.get('title','')[:60]}\n"
                    )
            return [types.TextContent(type="text", text=text)]

        else:
            raise ValueError(f"Unknown tool: {name}")
    finally:
        conn.close()


def _fmt(results: list[dict]) -> str:
    if not results:
        return "无结果"
    lines = []
    for r in results:
        lines.append(
            f"[{r.get('report_date','')}] {r.get('org','')} | "
            f"{r.get('stock','')}({r.get('stock_code','')}) | "
            f"{r.get('industry','')} | {r.get('em_rating','')}"
        )
        lines.append(f"  {r.get('title','')[:80]}")
        lines.append(f"  info_code: {r.get('info_code','')}")
        lines.append("")
    return "\n".join(lines)


def _fmt_semantic(results: list[dict]) -> str:
    if not results:
        return "无结果"
    lines = []
    for r in results:
        lines.append(
            f"[{r.get('report_date','')}] {r.get('org','')} | "
            f"{r.get('stock','')}({r.get('stock_code','')}) | "
            f"{r.get('industry','')} | 相似度: {r.get('distance','')}"
        )
        lines.append(f"  {r.get('title','')[:80]}")
        lines.append(f"  ...{r.get('chunk_text_snippet','')}...")
        lines.append("")
    return "\n".join(lines)


def _fmt_report(r: dict) -> str:
    lines = [
        f"标题: {r.get('title','')}",
        f"券商: {r.get('org','')}",
        f"股票: {r.get('stock','')}({r.get('stock_code','')})",
        f"行业: {r.get('industry','')}",
        f"评级: {r.get('em_rating','')}",
        f"日期: {r.get('report_date','')}",
        f"页数: {r.get('page_count','')}",
        "",
    ]
    for c in r.get("chunks", []):
        lines.append(f"[第{c['chunk_index']}段]")
        lines.append(c["chunk_text"][:500])
        lines.append("")
    return "\n".join(lines)


async def main():
    loop = asyncio.get_event_loop()

    read_send, read_stream = anyio.create_memory_object_stream(256)
    write_stream, write_recv = anyio.create_memory_object_stream(256)

    async def stdin_reader():
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
                if not line:
                    break
                try:
                    msg = types.JSONRPCMessage.model_validate_json(line.strip())
                except Exception as exc:
                    await read_send.send(exc)
                    continue
                await read_send.send(SessionMessage(msg))
        finally:
            await read_send.aclose()

    async def stdout_writer():
        try:
            while True:
                session_msg = await write_recv.receive()
                json_str = session_msg.message.model_dump_json(
                    by_alias=True, exclude_none=True
                )
                data = (json_str + "\n").encode()
                await loop.run_in_executor(None, sys.stdout.buffer.write, data)
                await loop.run_in_executor(None, sys.stdout.buffer.flush)
        except anyio.EndOfStream:
            pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdin_reader)
        tg.start_soon(stdout_writer)

        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="yanbao-mcp",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

        # Signal writer to stop after server.run() exits
        await write_send.aclose()


def run():
    import os

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except asyncio.CancelledError:
        pass

    # Force exit to prevent hanging on executor threads (sentence_transformers)
    os._exit(0)
