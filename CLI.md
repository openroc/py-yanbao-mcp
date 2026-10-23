# yanbao-mcp 命令行速查

本文是**当前代码的命令行速查表**（对照 `yanbao/cli.py` 核对）。完整原理说明、
数据库结构、索引管线见 [USAGE.md](USAGE.md)。

- 入口：`yanbao <命令>`（安装后）或 `python -m yanbao.cli <命令>`（源码目录）
- 查看某命令的全部选项：`yanbao <命令> --help`
- 常用后缀：`--json-out` 输出 JSON，便于脚本消费

## 命令速查表

| 命令 | 用途 |
|------|------|
| `yanbao download` | 从东方财富全量下载研报（JSON + PDF） |
| `yanbao update` | 按日期增量更新研报，可定时执行 |
| `yanbao fix` | 重新下载损坏的 PDF 并触发重建 |
| `yanbao index` | 建索引：增量 / 全量 / 仅元数据 |
| `yanbao status` | 查看索引进度与当前阶段 |
| `yanbao stats` | 查看库内统计（报告数、块数、覆盖范围） |
| `yanbao search` | 元数据 + FTS5 关键词检索 |
| `yanbao semantic` | 向量语义检索 |
| `yanbao report` | 查看单篇研报详情 |
| `yanbao by-stock` | 按股票代码列研报 |
| `yanbao by-industry` | 按行业列研报 |
| `yanbao chat` | 基于研报库问答（RAG） |
| `yanbao summary` | 单篇研报摘要（LLM） |
| `yanbao analyze` | 多研报综合分析（LLM） |
| `yanbao serve` | 启动 MCP Server |

---

## 1. 下载与更新

### yanbao download

全量下载，结束后**自动执行一次增量索引**。

```bash
yanbao download --qps 2.0 --max-pages 5     # 先试跑 5 页
yanbao download --qps 1.0 --min-qps 0.02    # 全量（保守限速）
yanbao download --no-index                   # 只下载不索引
yanbao download --page-size 100              # 每页 100 条（最大 500）
```

| 选项 | 默认 | 说明 |
|------|------|------|
| `--qps` | 2.0 | 初始每秒请求数 |
| `--min-qps` | 0.05 | 触发退避后的最低 QPS |
| `--max-pages` | 0 | 最大页数（0=不限） |
| `--page-size` | 50 | 每页条数 |
| `--no-index` | — | 下载后不自动索引 |

### yanbao update

按日期增量拉取，适合每日定时。默认范围为「昨天 ~ 今天」。

```bash
yanbao update                                # 默认日期范围
yanbao update --date-from 2026-10-01 --date-to 2026-10-20
yanbao update --no-index                     # 不自动索引
```

### yanbao fix

重新下载已损坏的 PDF，把这些研报标记为待重建并自动增量索引。

```bash
yanbao fix
yanbao fix --no-index
```

---

## 2. 索引

### yanbao index

四种模式，**不带参数默认走增量索引**。

```bash
yanbao index                     # 增量：只处理新增/变更文件
yanbao index --incremental       # 同上（显式）
yanbao index --full              # 全量：metadata + PDF + 向量，支持断点续传
yanbao index --full --workers 8  # 全量并行（8 个 worker，推荐）
yanbao index --metadata-only     # 只索引元数据，最快
yanbao index --full --reset      # 清空数据重来（会二次确认）
```

| 选项 | 说明 |
|------|------|
| `--full` | 全量索引，断点续传 |
| `--workers N` | 并行 worker 数（0=单线程），仅 `--full` 生效 |
| `--metadata-only` | 只写 `reports` 元数据，不解析 PDF |
| `--incremental` | 只处理新增/变更的 PDF |
| `--reset` | 与 `--full` 同用：先清空 reports/text_chunks/向量再重建 |

索引管线顺序：**metadata → PDF 文本抽取 → 文本分块 → 向量 → 因子提取**。
各阶段均断点续传，中断后重跑会跳过已完成部分。

### yanbao status / yanbao stats

```bash
yanbao status            # 报告总数、已抽 PDF 数、已建块数、已建向量数、当前阶段
yanbao status --json-out
yanbao stats             # 报告数、块数、覆盖标的/行业/券商、日期范围
```

> `status` 里「向量已索引」一栏统计的是**已有向量的研报数**（不是向量条数）。
> 统计走的是全表扫描 + Python 侧比对，不做跨表 JOIN——sqlite-vec 的 JOIN 是
> 逐行 KNN 查询，几十万行 JOIN 会跑到分钟级。

---

## 3. 检索

### yanbao search

元数据多条件组合过滤 + FTS5 全文关键词检索（走 `reports_fts`）。

```bash
yanbao search --keyword "半导体"
yanbao search --stock-code 600988
yanbao search --stock 韦尔股份
yanbao search --industry 新能源 --org 中信证券
yanbao search --keyword 涨价 --date-from 2026-01-01 --date-to 2026-06-30 --limit 50
yanbao search --keyword 产能 --json-out
```

选项：`--stock-code` `--stock` `--industry` `--org` `--keyword`
`--date-from` `--date-to`（`YYYY-MM-DD`）`--limit`（默认 20）`--json-out`

### yanbao semantic

向量语义检索（余弦距离排序）。

```bash
yanbao semantic "光伏组件涨价对毛利率的影响"
yanbao semantic "公司产能利用率" --top-k 5
yanbao semantic "锂价下行" --json-out
```

### yanbao report / by-stock / by-industry

```bash
yanbao report AP202409031639720862
yanbao report AP202409031639720862 --json-out
yanbao by-stock 600988 --limit 50
yanbao by-industry 半导体 --limit 20
```

---

## 4. LLM 功能

需要配置 `YANBAO_LLM_*`（见 `.env`），服务需兼容 OpenAI API。

```bash
yanbao chat "半导体板块未来一年的景气度如何" --top-k 8
yanbao summary AP202409031639720862
yanbao analyze "对比这几家光伏公司的产能规划" --top-k 10
```

- `chat`：检索相关片段 → LLM 作答 → 附参考来源
- `summary`：单篇研报摘要
- `analyze`：跨多篇研报综合分析

LLM 未配置或调用失败时，命令会打印错误信息而不是抛出栈。

---

## 5. MCP Server

```bash
yanbao serve
```

stdio 传输（每行一个 JSON-RPC 消息）。暴露 10 个工具：

| 工具 | 说明 |
|------|------|
| `search_reports` | 元数据 + 关键词检索 |
| `semantic_search` | 语义搜索 |
| `get_report` | 单篇研报详情 |
| `list_by_stock` | 按股票列研报 |
| `list_by_industry` | 按行业列研报 |
| `get_stats` | 库内统计 |
| `ask` | RAG 问答 |
| `search_factors` | 因子检索（按类型/名称/值过滤） |
| `semantic_factor_search` | 因子语义检索 |
| `get_report_factors` | 单篇研报的全部因子 |

> 因子检索**只在 MCP 层提供**，CLI 没有对应的子命令。

客户端配置示例：

```json
{
  "mcpServers": {
    "yanbao-mcp": {
      "command": "yanbao",
      "args": ["serve"],
      "env": { "YANBAO_DATA_DIR": "H:/tmp/reports", "YANBAO_DB_PATH": "H:/code/yanbao-mcp/data/reports.db" }
    }
  }
}
```

---

## 6. 辅助脚本（源码根目录，非 CLI 子命令）

| 脚本 | 用途 |
|------|------|
| `rechunk_and_reindex.py` | 不重读 PDF 重建文本块与向量（见下） |
| `demo_factors.py` | 因子提取/检索的手工演示 |
| `init_factor_tables.py` | 单独初始化因子相关表 |

### rechunk_and_reindex.py

用于**分块规则变更后**的存量数据重建：把每篇研报的旧块拼回原文，用当前
`chunk_text` 重新切分并重新生成向量。**会删除并重写全部文本块与向量，执行前
务必备份数据库。**

```bash
cp data/reports.db data/reports.db.bak
python rechunk_and_reindex.py                 # 全量重建
python rechunk_and_reindex.py --limit 200     # 只处理前 200 篇（试跑）
python rechunk_and_reindex.py --no-vectors    # 只重建块，不生成向量
```

可重复执行：块长度已合规且向量齐全的研报会被自动跳过，中断后重跑即可续上。

---

## 7. 配置速查（`.env`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `YANBAO_DATA_DIR` | `H:/tmp/reports` | 研报 JSON/PDF 根目录 |
| `YANBAO_DB_PATH` | `data/reports.db` | SQLite 库路径 |
| `YANBAO_EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 向量模型 |
| `YANBAO_CHUNK_SIZE` | `512` | 单块字符上限（= 模型 token 上限） |
| `YANBAO_CHUNK_OVERLAP` | `64` | 块间重叠字符数 |
| `YANBAO_INDEX_STATE` | `data/.index_state.json` | 断点续传状态文件 |
| `YANBAO_LOG_LEVEL` | `INFO` | 日志级别 |
| `YANBAO_LLM_ENABLED` | `true` | 是否启用 LLM 功能 |
| `YANBAO_LLM_BASE_URL` | `http://localhost:8080/v1` | OpenAI 兼容端点 |
| `YANBAO_LLM_MODEL` | — | 模型名 |
| `YANBAO_LLM_API_KEY` | — | 密钥 |
| `HF_ENDPOINT` | — | HuggingFace 镜像（如 `https://hf-mirror.com`） |

向量维度固定为 **512**（`bge-small-zh-v1.5` 的 hidden_size），与
`yanbao/config.py` 的 `EMBEDDING_DIM` 一致；改动模型必须同步改维度并重建库。

---

## 8. 常见注意事项

- **索引前先看 `status`**：确认上次中断在哪个阶段，直接重跑 `yanbao index` 即可续传。
- **`--reset` 会清空数据**：仅在确认要重来时使用，会弹二次确认。
- **向量不可用时**：`semantic` 会明确报「embedding 模型未加载」，而不是静默返回空。
- **大库避免跨表 JOIN vec0**：sqlite-vec 的向量表 JOIN 是逐行 KNN 查询，普通表
  关联请在 Python 侧做集合比对（`db.vectorized_chunk_ids` / `db.vectorized_factor_ids`
  已封装好这个模式）。
- **Windows 控制台编码**：中文日志乱码时加 `PYTHONIOENCODING=utf-8`。
