import io
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from . import db as db_module
from . import search as search_module
from .config import DB_PATH, LOG_LEVEL
from .downloader import YanbaoDownloader
from .indexer import run_full_index, run_full_index_parallel, run_incremental, run_metadata_only

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("yanbao")


@click.group()
def cli():
    pass


@cli.command()
@click.option("--full", is_flag=True, help="全量索引 (metadata + PDF + vectors，断点续传)")
@click.option("--reset", is_flag=True, help="重置数据库后重新全量索引（慎用）")
@click.option("--metadata-only", is_flag=True, help="仅索引元数据 (快速)")
@click.option("--incremental", is_flag=True, help="增量索引 (仅新增/变更文件)")
@click.option("--workers", default=0, type=int, help="并行 worker 数 (默认0=单线程)")
def index(full, reset, metadata_only, incremental, workers):
    conn = db_module.get_connection()
    db_module.init_db(conn)
    if reset and full:
        click.confirm("将清空数据库重新索引，确定？", abort=True)
        conn.execute("DELETE FROM chunk_vectors")
        conn.execute("DELETE FROM text_chunks")
        conn.executescript("DELETE FROM reports_fts; DELETE FROM reports;")
        conn.commit()
        if workers > 0:
            run_full_index_parallel(conn, num_workers=workers, reset=True)
        else:
            run_full_index(conn, reset=True)
    elif metadata_only:
        run_metadata_only(conn)
    elif full:
        if workers > 0:
            run_full_index_parallel(conn, num_workers=workers)
        else:
            run_full_index(conn)
    elif incremental:
        run_incremental(conn)
    else:
        run_incremental(conn)
    conn.close()


@cli.command()
@click.option("--stock-code", help="股票代码")
@click.option("--stock", help="股票名称")
@click.option("--industry", help="行业")
@click.option("--org", help="券商")
@click.option("--keyword", help="关键词")
@click.option("--date-from", help="起始日期 YYYY-MM-DD")
@click.option("--date-to", help="结束日期 YYYY-MM-DD")
@click.option("--limit", default=20, type=int)
@click.option("--json-out", is_flag=True, help="JSON格式输出")
def search(stock_code, stock, industry, org, keyword, date_from, date_to, limit, json_out):
    conn = db_module.get_connection()
    results = search_module.search_metadata(
        conn,
        query=keyword,
        stock_code=stock_code,
        stock_name=stock,
        industry=industry,
        org=org,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    conn.close()
    _output(results, json_out)


@cli.command()
@click.argument("query")
@click.option("--top-k", default=10, type=int)
@click.option("--json-out", is_flag=True)
def semantic(query, top_k, json_out):
    conn = db_module.get_connection()
    results = search_module.semantic_search(conn, query, top_k=top_k)
    conn.close()
    _output(results, json_out)


@cli.command()
@click.argument("info_code")
@click.option("--json-out", is_flag=True)
def report(info_code, json_out):
    conn = db_module.get_connection()
    result = search_module.get_report(conn, info_code)
    conn.close()
    if result is None:
        click.echo("Not found")
        return
    _output([result], json_out)


@cli.command()
@click.argument("stock_code")
@click.option("--limit", default=50, type=int)
@click.option("--json-out", is_flag=True)
def by_stock(stock_code, limit, json_out):
    conn = db_module.get_connection()
    results = search_module.list_by_stock(conn, stock_code, limit=limit)
    conn.close()
    _output(results, json_out)


@cli.command()
@click.argument("industry")
@click.option("--limit", default=20, type=int)
@click.option("--json-out", is_flag=True)
def by_industry(industry, limit, json_out):
    conn = db_module.get_connection()
    results = search_module.list_by_industry(conn, industry, limit=limit)
    conn.close()
    _output(results, json_out)


@cli.command()
@click.option("--json-out", is_flag=True)
def status(json_out):
    """查看索引进度"""
    import json as _json
    from pathlib import Path
    from .config import INDEX_STATE_PATH, PROJECT_DIR
    db_path = PROJECT_DIR / "data" / "reports.db"
    if not db_path.exists():
        click.echo("数据库不存在，尚未执行过索引")
        return
    conn = db_module.get_connection()
    total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    with_pdf = conn.execute("SELECT COUNT(*) FROM reports WHERE page_count > 0").fetchone()[0]
    with_chunks = conn.execute("SELECT COUNT(DISTINCT info_code) FROM text_chunks").fetchone()[0]
    with_vectors = conn.execute(
        "SELECT COUNT(DISTINCT tc.info_code) FROM text_chunks tc JOIN chunk_vectors cv ON tc.chunk_id = cv.chunk_id"
    ).fetchone()[0]
    conn.close()
    state = {}
    if INDEX_STATE_PATH.exists():
        with open(INDEX_STATE_PATH, encoding="utf-8") as f:
            state = _json.load(f)
    phase = state.get("phase", "unknown")
    state_total = state.get("total", 0)
    state_done = state.get("done", 0)
    if json_out:
        click.echo(_json.dumps({
            "total_reports": total,
            "pdf_extracted": with_pdf,
            "chunks_indexed": with_chunks,
            "vectors_indexed": with_vectors,
            "pdf_pending": total - with_pdf,
            "phase": phase,
            "phase_progress": f"{state_done}/{state_total}" if state_total else "",
            "last_run": state.get("last_run", ""),
        }, ensure_ascii=False, indent=2))
    else:
        click.echo(f"报告总数:        {total}")
        click.echo(f"PDF已提取:       {with_pdf} (待处理: {total - with_pdf})")
        click.echo(f"文本块已索引:     {with_chunks}")
        click.echo(f"向量已索引:       {with_vectors}")
        click.echo(f"当前阶段:        {phase}", nl=False)
        if state_total > 0:
            pct = state_done / state_total * 100
            click.echo(f" [{state_done}/{state_total} {pct:.1f}%]")
        else:
            click.echo("")
        click.echo(f"上次运行:        {state.get('last_run', 'N/A')}")
        if phase == "complete" or with_vectors == total:
            click.echo(">>> 全量索引已完成 <<<")


@cli.command()
@click.option("--json-out", is_flag=True)
def stats(json_out):
    conn = db_module.get_connection()
    s = db_module.get_stats(conn)
    conn.close()
    if json_out:
        click.echo(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        click.echo(f"报告总数: {s['total_reports']}")
        click.echo(f"文本块数: {s['total_chunks']}")
        click.echo(f"覆盖标的: {s['unique_stocks']}")
        click.echo(f"覆盖行业: {s['unique_industries']}")
        click.echo(f"覆盖券商: {s['unique_orgs']}")
        click.echo(f"日期范围: {s['date_range'][0]} ~ {s['date_range'][1]}")


@cli.command()
@click.option("--qps", default=2.0, type=float, help="初始 QPS (默认 2.0)")
@click.option("--min-qps", default=0.05, type=float, help="退避最低 QPS (默认 0.05)")
@click.option("--max-pages", default=0, type=int, help="最大页数 (0=不限)")
@click.option("--page-size", default=50, type=int, help="每页条数 (默认 50)")
@click.option("--no-index", is_flag=True, help="下载后不自动增量索引")
def download(qps, min_qps, max_pages, page_size, no_index):
    """全量下载研报 (从东方财富)"""
    d = YanbaoDownloader(max_qps=qps, min_qps=min_qps, max_pages=max_pages, page_size=page_size)
    d.run()
    if not no_index:
        _run_auto_index()


@cli.command()
@click.option("--date-from", default="", help="起始日期 (默认昨天, YYYY-MM-DD)")
@click.option("--date-to", default="", help="结束日期 (默认今天, YYYY-MM-DD)")
@click.option("--qps", default=2.0, type=float, help="初始 QPS (默认 2.0)")
@click.option("--no-index", is_flag=True, help="更新后不自动增量索引")
def update(date_from, date_to, qps, no_index):
    """增量更新研报 (每日定时执行)"""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        yesterday = datetime.now() - timedelta(days=1)
        date_from = yesterday.strftime("%Y-%m-%d")
    d = YanbaoDownloader(max_qps=qps)
    d.run(date_from=date_from, date_to=date_to)
    if not no_index:
        _run_auto_index()


def _run_auto_index():
    click.echo("")
    click.echo(">>> 自动执行增量索引...")
    conn = db_module.get_connection()
    db_module.init_db(conn)
    run_incremental(conn)
    conn.close()


@cli.command()
@click.option("--qps", default=2.0, type=float, help="初始 QPS (默认 2.0)")
@click.option("--no-index", is_flag=True, help="修复后不自动增量索引")
def fix(qps, no_index):
    """重新下载受损 PDF 并重新索引"""
    d = YanbaoDownloader(max_qps=qps)
    fixed_codes = d.fix_corrupted()
    if not fixed_codes:
        return

    conn = db_module.get_connection()
    db_module.init_db(conn)
    for code in fixed_codes:
        conn.execute("DELETE FROM text_chunks WHERE info_code=?", (code,))
        conn.execute("UPDATE reports SET page_count=0 WHERE info_code=?", (code,))
    conn.commit()
    logger.info("Reset %d reports for re-index", len(fixed_codes))
    conn.close()

    if not no_index:
        click.echo("")
        click.echo(">>> 自动执行增量索引...")
        conn = db_module.get_connection()
        db_module.init_db(conn)
        run_incremental(conn)
        conn.close()


@cli.command()
def serve():
    from .mcp_server import run as mcp_run
    mcp_run()


@cli.command()
@click.argument("query")
@click.option("--top-k", default=8, type=int, help="检索相关片段数")
def chat(query, top_k):
    """基于研报知识库问答"""
    from . import llm as llm_module
    try:
        result = llm_module.rag_answer(query, top_k=top_k)
    except RuntimeError as e:
        click.echo(f"错误: {e}")
        return
    except Exception as e:
        click.echo(f"LLM 调用失败: {e}")
        return
    click.echo(result["answer"])
    if result.get("citations"):
        click.echo("\n--- 参考来源 ---")
        for r in result["citations"]:
            click.echo(
                f"  [{r.get('report_date','')}] {r.get('org','')} | "
                f"{r.get('stock','')}({r.get('stock_code','')}) | "
                f"{r.get('title','')[:60]}"
            )


@cli.command()
@click.argument("info_code")
def summary(info_code):
    """摘要单篇研报"""
    from . import llm as llm_module
    try:
        result = llm_module.summarize(info_code)
    except RuntimeError as e:
        click.echo(f"错误: {e}")
        return
    except Exception as e:
        click.echo(f"LLM 调用失败: {e}")
        return
    click.echo(result)


@cli.command()
@click.argument("query")
@click.option("--top-k", default=10, type=int, help="检索相关片段数")
def analyze(query, top_k):
    """多研报综合分析"""
    from . import llm as llm_module
    try:
        result = llm_module.synthesize(query, top_k=top_k)
    except RuntimeError as e:
        click.echo(f"错误: {e}")
        return
    except Exception as e:
        click.echo(f"LLM 调用失败: {e}")
        return
    click.echo(result["answer"])
    if result.get("citations"):
        click.echo("\n--- 参考来源 ---")
        for r in result["citations"]:
            click.echo(
                f"  [{r.get('report_date','')}] {r.get('org','')} | "
                f"{r.get('stock','')}({r.get('stock_code','')}) | "
                f"{r.get('title','')[:60]}"
            )


def _output(results: list, json_out: bool):
    if json_out:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if not results:
        click.echo("无结果")
        return
    for r in results:
        title = r.get("title", "")[:60]
        stock = r.get("stock", "")
        code = r.get("stock_code", "")
        industry = r.get("industry", "")
        org = r.get("org", "")
        date = r.get("report_date", "")
        rating = r.get("em_rating", "")
        dist = r.get("distance")
        dist_str = f" [dist={dist:.3f}]" if dist is not None else ""
        click.echo(f"{date} | {org} | {stock}({code}) | {industry} | {rating}{dist_str}")
        click.echo(f"   {title}")
        click.echo("")


if __name__ == "__main__":
    cli()
