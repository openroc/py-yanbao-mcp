import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'H:/code/yanbao-mcp')
from yanbao.db import get_connection, init_db, init_factor_tables

conn = get_connection()
init_db(conn)
init_factor_tables(conn)
print('Factor tables initialized successfully')

# Verify
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
for t in tables:
    print(f'  - {t[0]}')
conn.close()
