"""Test script for yanbao-mcp tools.

Tests the underlying search functions directly (same logic as MCP server tools).

Usage:
    python tests/test_tools.py
    python tests/test_tools.py --mcp    # also test MCP server (needs conda env)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yanbao import db as db_module
from yanbao import search as search_module


def _get_conn():
    conn = db_module.get_connection()
    db_module.init_db(conn)
    return conn


def _has_data(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] > 0


def test_search_reports():
    conn = _get_conn()
    if not _has_data(conn):
        conn.close()
        return "SKIP: no data"
    r = search_module.search_metadata(conn, query="新能源", limit=5)
    assert len(r) <= 5
    r2 = search_module.search_metadata(conn, stock_code="600519", limit=5)
    assert len(r2) <= 5
    conn.close()
    return "PASS"


def test_semantic_search():
    conn = _get_conn()
    if not _has_data(conn):
        conn.close()
        return "SKIP: no data"
    has_vec = conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
    if has_vec == 0:
        conn.close()
        return "SKIP: no vectors"
    r = search_module.semantic_search(conn, "锂电池", top_k=3)
    assert len(r) <= 3
    conn.close()
    return "PASS"


def test_get_report():
    conn = _get_conn()
    if not _has_data(conn):
        conn.close()
        return "SKIP: no data"
    code = conn.execute("SELECT info_code FROM reports LIMIT 1").fetchone()
    if not code:
        conn.close()
        return "SKIP: no report"
    r = search_module.get_report(conn, code["info_code"])
    assert r is not None and "title" in r
    conn.close()
    return "PASS"


def test_list_by_stock():
    conn = _get_conn()
    if not _has_data(conn):
        conn.close()
        return "SKIP: no data"
    code = conn.execute(
        "SELECT stock_code FROM reports WHERE stock_code != '' LIMIT 1"
    ).fetchone()
    if not code:
        conn.close()
        return "SKIP: no stock"
    r = search_module.list_by_stock(conn, code["stock_code"], limit=3)
    assert len(r) <= 3
    conn.close()
    return "PASS"


def test_list_by_industry():
    conn = _get_conn()
    if not _has_data(conn):
        conn.close()
        return "SKIP: no data"
    ind = conn.execute(
        "SELECT industry FROM reports WHERE industry != '' LIMIT 1"
    ).fetchone()
    if not ind:
        conn.close()
        return "SKIP: no industry"
    r = search_module.list_by_industry(conn, ind["industry"], limit=3)
    assert len(r) <= 3
    conn.close()
    return "PASS"


def test_get_stats():
    conn = _get_conn()
    if not _has_data(conn):
        conn.close()
        return "SKIP: no data"
    s = db_module.get_stats(conn)
    assert s["total_reports"] > 0
    conn.close()
    return "PASS"


def test_mcp_smoke():
    import json, subprocess
    proc = subprocess.Popen(
        [sys.executable, "-m", "yanbao.cli", "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parent.parent),
    )

    init = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1.0"},
        },
    })
    proc.stdin.write(f"Content-Length: {len(init)}\r\n\r\n{init}".encode())
    proc.stdin.flush()

    header = b""
    deadline = time.time() + 8
    while time.time() < deadline:
        ch = proc.stdout.read(1)
        if not ch:
            break
        header += ch
        if header.endswith(b"\r\n\r\n"):
            break

    if not header:
        proc.kill()
        return "FAIL: timeout (server may need conda env)"

    length = 0
    for line in header.decode().split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":")[1].strip())

    body = b""
    if length:
        import threading
        def _read():
            nonlocal body
            body = proc.stdout.read(length)
        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=5)
        if t.is_alive():
            proc.kill()
            return "FAIL: body read timeout"
    proc.terminate()
    result = json.loads(body.decode())
    assert result.get("id") == 1
    return f"PASS (protocol {result['result'].get('protocolVersion')})"


if __name__ == "__main__":
    run_mcp = "--mcp" in sys.argv

    tests = [
        test_semantic_search,
        test_search_reports,  
        test_get_report,
        test_list_by_stock, 
        test_list_by_industry, 
        test_get_stats,
    ]
    if run_mcp:
        tests.append(test_mcp_smoke)

    passed = failed = skipped = 0
    for t in tests:
        name = t.__name__.replace("test_", "")
        try:
            result = t()
            if result.startswith("SKIP"):
                print(f"  {name:<20} {result}")
                skipped += 1
            else:
                print(f"  {name:<20} {result}")
                passed += 1
        except Exception as e:
            print(f"  {name:<20} FAIL: {e}")
            failed += 1

    total = passed + failed + skipped
    print(f"\n  Passed: {passed}, Failed: {failed}, Skipped: {skipped}  (total {total})")
    sys.exit(1 if failed else 0)
