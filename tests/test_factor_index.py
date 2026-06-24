import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'H:/code/yanbao-mcp')

from yanbao.db import get_connection, init_db, init_factor_tables
from yanbao.indexer import index_factors, index_factor_vectors

conn = get_connection()
init_db(conn)
init_factor_tables(conn)

# 选取一个研报测试
row = conn.execute("SELECT info_code, pdf_path FROM reports LIMIT 1").fetchone()
if row:
    info_code = row[0]
    pdf_path = row[1]
    print(f"测试研报: {info_code}")
    print(f"PDF路径: {pdf_path}")
    
    # 检查是否已有因子
    existing = conn.execute(
        "SELECT COUNT(*) FROM factor_chunks WHERE info_code = ?", (info_code,)
    ).fetchone()[0]
    print(f"已有因子块: {existing}")
    
    # 提取因子
    print("提取因子中...")
    n = index_factors(conn, info_code)
    print(f"提取到 {n} 个因子")
    
    # 生成因子向量
    if n > 0:
        print("生成因子向量中...")
        nv = index_factor_vectors(conn, info_code)
        print(f"生成 {nv} 个因子向量")
    
    # 查询验证
    factors = conn.execute(
        "SELECT factor_type, factor_name, value, unit FROM factors f JOIN factor_chunks fc ON f.chunk_id = fc.chunk_id WHERE fc.info_code = ?",
        (info_code,)
    ).fetchall()
    print(f"\n因子列表:")
    for f in factors:
        print(f"  [{f[0]}] {f[1]}: {f[2]} {f[3]}")
else:
    print("没有研报数据，请先运行 yanbao index 或下载研报")

conn.close()
