# yanbao-mcp 使用手册

A 股券商研报本地知识库。支持东方财富研报下载、元数据检索、FTS5 全文搜索、语义向量搜索，提供 CLI 和 MCP Server 两种使用方式。

## 目录

- [安装](#安装)
- [配置](#配置)
- [数据目录结构](#数据目录结构)
- [CLI 命令](#cli-命令)
  - [yanbao download](#yanbao-download)
  - [yanbao update](#yanbao-update)
  - [yanbao index](#yanbao-index)
  - [yanbao search](#yanbao-search)
  - [yanbao semantic](#yanbao-semantic)
  - [yanbao report](#yanbao-report)
  - [yanbao by-stock](#yanbao-by-stock)
  - [yanbao by-industry](#yanbao-by-industry)
  - [yanbao status](#yanbao-status)
  - [yanbao stats](#yanbao-stats)
  - [yanbao serve](#yanbao-serve)
- [MCP Server](#mcp-server)
- [数据库结构](#数据库结构)
- [索引管线](#索引管线)
- [搜索](#搜索)
- [下载限流策略](#下载限流策略)
- [损坏 PDF 处理](#损坏-pdf-处理)
- [常见问题](#常见问题)

---

## 安装

```bash
# 1. 克隆仓库
git clone git@github.com:openroc/py-yanbao-mcp.git
cd py-yanbao-mcp

# 2. 安装依赖（建议 conda 环境）
pip install -r requirements.txt
pip install -e .
```

依赖清单见 `pyproject.toml`，核心依赖：

| 包 | 用途 |
|------|---------|
| `click` | CLI 框架 |
| `requests` | 下载 HTTP 请求 |
| `mcp` | MCP 协议服务端 |
| `pypdf` | PDF 文本提取 |
| `sentence-transformers` | 向量嵌入模型 |
| `sqlite-vec` | SQLite 向量索引扩展 |
| `numpy` | 向量计算 |
| `chardet` | 文件编码检测 |

---

## 配置

通过 `.env` 文件或环境变量配置，缺省值如下：

```ini
# 研报数据根目录（JSON + PDF）
YANBAO_DATA_DIR=H:/tmp/reports

# SQLite 数据库路径
YANBAO_DB_PATH=data/reports.db

# 嵌入模型（HuggingFace 模型名或本地路径）
YANBAO_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5

# 文本分块参数
YANBAO_CHUNK_SIZE=512          # 每个块的最大字符数
YANBAO_CHUNK_OVERLAP=64        # 块间重叠字符数

# 索引状态文件
YANBAO_INDEX_STATE=data/.index_state.json

# 损坏 PDF 记录文件
YANBAO_CORRUPTED_PDFS=data/corrupted_pdfs.jsonl

# 日志级别
YANBAO_LOG_LEVEL=INFO
```

---

## 数据目录结构

`YANBAO_DATA_DIR` 下按券商组织：

```
H:/tmp/reports/
├── 中信证券/
│   ├── 20260601_某公司深度研究报告.json
│   ├── 20260601_某公司深度研究报告.pdf
│   ├── 20260602_行业周报.json
│   └── 20260602_行业周报.pdf
├── 华泰证券/
├── 国泰君安/
├── ... (69 家券商)
└── .download_state.json       # 下载器状态（下载去重）
```

每条研报包含两个文件：

- **`*.json`** — 元数据，字段：
  ```json
  {
      "infoCode": "AP202606010001",
      "title": "公司2025年年报点评：业绩超预期",
      "org": "中信证券",
      "stock": "贵州茅台",
      "stockCode": "600519",
      "industry": "白酒",
      "emRating": "买入",
      "authors": ["张三", "李四"],
      "date": "2026-06-01",
      "reportUrl": "https://data.eastmoney.com/report/600519/AP202606010001.html",
      "pdfUrl": "https://pdf.dfcfw.com/pdf/H3_AP202606010001_1.pdf"
  }
  ```
- **`*.pdf`** — 研报原文 PDF

---

## CLI 命令

### yanbao download

从东方财富全量下载研报，默认下载后续自动执行增量索引。

```bash
# 快速测试（只下载前 5 页）
yanbao download --qps 2.0 --max-pages 5

# 全量下载（不限页数，保守限速）
yanbao download --qps 1.0 --min-qps 0.02

# 下载后不自动索引
yanbao download --no-index

# 指定每页条数
yanbao download --page-size 100
```

| 选项 | 默认 | 说明 |
|------|------|------|
| `--qps` | 2.0 | 初始每秒请求数 |
| `--min-qps` | 0.05 | 退避后最低 QPS |
| `--max-pages` | 0 | 最大页数（0=不限） |
| `--page-size` | 50 | 每页条数（最大 500） |
| `--no-index` | -- | 下载后不执行增量索引 |

下载路径：`YANBAO_DATA_DIR / 券商名 / {日期}_{标题}.json` + `.pdf`。

### yanbao update

每日定时增量更新，按日期范围拉取新研报。

```bash
# 默认更新昨天到今天
yanbao update

# 指定日期范围
yanbao update --date-from 2026-06-15 --date-to 2026-06-17

# 更新后不自动索引
yanbao update --no-index
```

| 选项 | 默认 | 说明 |
|------|------|------|
| `--date-from` | 昨天 | 起始日期 YYYY-MM-DD |
| `--date-to` | 今天 | 结束日期 YYYY-MM-DD |
| `--qps` | 2.0 | 初始每秒请求数 |
| `--no-index` | -- | 更新后不执行增量索引 |

**定时任务示例（Windows 任务计划程序）：**

```
触发器: 每天 09:00
操作:   powershell -Command "conda activate base && cd H:\code\yanbao-mcp && yanbao update >> update.log 2>&1"
```

### yanbao index

将 `YANBAO_DATA_DIR` 下的研报文件索引到 SQLite 数据库。

```bash
# 增量索引（默认，只处理新增/变更文件）
yanbao index

# 全量索引（扫描所有文件，断点续传，单线程）
yanbao index --full

# 全量索引 + 8 并行 worker（处理大量 PDF 时推荐）
yanbao index --full --workers 8

# 仅索引元数据（不提取 PDF 文本，快速）
yanbao index --metadata-only

# 重置数据库后重新全量索引（清空现有数据）
yanbao index --full --reset
```

| 选项 | 说明 |
|------|------|
| `--full` | 全量索引（元数据 → PDF 提取 → 向量嵌入），支持断点续传 |
| `--reset` | 重置数据库后全量索引，需与 `--full` 联用 |
| `--metadata-only` | 仅扫描元数据入库，不处理 PDF |
| `--incremental` | 增量索引（默认模式），MD5 检测变更 |
| `--workers N` | 并行 Worker 数（默认 0=单线程，建议 4~8） |

**索引阶段说明：**

1. **metadata** — 扫描全部 JSON 文件，写入 `reports` 表
2. **pdf_extraction** — 提取 PDF 文本 → 分块 → 写入 `text_chunks` 表
3. **vector_generation** — 生成文本向量 → 写入 `chunk_vectors` 表（需 sentence-transformers 模型加载成功）

**并行模式特点：**
- `--workers 0`：单线程顺序处理，适合小量更新
- `--workers N`：`multiprocessing.Pool` + `apply_async` 轮询，支持 Ctrl+C 平滑退出
- 任务分批：4×Worker 数个小批次，`imap_unordered` 风格负载均衡
- 断点续传：重复运行自动跳过已处理的 PDF（`page_count > 0`）

### yanbao search

元数据 + FTS5 全文检索。

```bash
# 关键词搜索
yanbao search --keyword "半导体"

# 按股票代码 + 券商
yanbao search --stock-code 600519 --org "中信证券"

# 按行业 + 日期范围
yanbao search --industry "新能源" --date-from 2026-01-01 --date-to 2026-06-30

# 多条件组合 + JSON 输出
yanbao search --keyword "AI" --stock "宁德时代" --limit 10 --json-out
```

| 选项 | 说明 |
|------|------|
| `--keyword` | FTS5 关键词匹配标题/股票/行业/券商 |
| `--stock-code` | 股票代码精确匹配 |
| `--stock` | 股票名称模糊匹配 |
| `--industry` | 行业模糊匹配 |
| `--org` | 券商模糊匹配 |
| `--date-from` | 起始日期 YYYY-MM-DD |
| `--date-to` | 结束日期 YYYY-MM-DD |
| `--limit` | 返回条数上限（默认 20） |
| `--json-out` | JSON 格式输出 |

**CJK 特殊处理：** 中文关键词按单字符分词 + FTS5 前缀匹配 + `LIKE` 回退；ASCII 关键词正常 FTS5 短语匹配。

### yanbao semantic

语义向量搜索（自然语言查询）。

```bash
# 基础语义搜索
yanbao semantic "哪些公司最近被券商首次评级为买入"

# 限定返回条数
yanbao semantic "新能源汽车产业链上游企业" --top-k 5

# JSON 输出
yanbao semantic "半导体设备国产替代进展" --json-out
```

| 选项 | 默认 | 说明 |
|------|------|------|
| `query` | (必填) | 自然语言查询 |
| `--top-k` | 10 | 返回结果数量 |
| `--json-out` | -- | JSON 格式输出，含匹配文本片段 `chunk_text_snippet` |

> 注意：需先完成向量索引阶段（`yanbao index --full`），否则返回空结果。如果 sentence-transformers 模型加载失败，会跳过向量阶段并提示。

### yanbao report

查看单篇研报的完整元数据和文本块。

```bash
yanbao report AP202606010001
yanbao report AP202606010001 --json-out
```

返回：研报基本信息 + 所有分块文本内容。

### yanbao by-stock

按股票代码查询所有研报。

```bash
yanbao by-stock 600519
yanbao by-stock 300750 --limit 10
```

### yanbao by-industry

按行业查询研报。

```bash
yanbao by-industry 新能源
yanbao by-industry 半导体 --limit 5
```

### yanbao status

查看索引进度。

```bash
yanbao status
yanbao status --json-out
```

输出示例：
```
报告总数:        28397
PDF已提取:       4358 (待处理: 24039)
文本块已索引:    4275
向量已索引:      0
当前阶段:        pdf_extraction [4250/28313 15.0%]
上次运行:        N/A
```

### yanbao stats

查看知识库统计概览。

```bash
yanbao stats
yanbao stats --json-out
```

输出示例：
```
报告总数: 28397
文本块数: 4275
覆盖标的: 2997
覆盖行业: 163
覆盖券商: 69
日期范围: 2024-09-02 ~ 2026-06-17
```

### yanbao serve

启动 MCP Server（与 MCP 客户端交互）。

```bash
yanbao serve
```

启动后通过标准输入/输出与 MCP 客户端通信。详见下方 [MCP Server](#mcp-server) 章节。

---

## MCP Server

yanbao-mcp 实现了 MCP (Model Context Protocol) 服务器，可与 opencode、Claude Desktop 等 MCP 客户端集成。

### 启动

```bash
yanbao serve
```

### 暴露的工具

| 工具名 | 描述 | 主要参数 |
|---------|-------------|-----------|
| `search_reports` | 元数据 + 关键词检索 | `query`, `stock_code`, `industry`, `org`, `date_from`, `date_to`, `limit` |
| `semantic_search` | 语义向量搜索 | `query`, `top_k` |
| `get_report` | 获取单篇研报全文 | `info_code` |
| `list_by_stock` | 某股票全部研报 | `stock_code`, `limit` |
| `list_by_industry` | 某行业研报列表 | `industry`, `limit` |
| `get_stats` | 知识库统计 | 无 |

### 集成到 opencode

编辑 `opencode.json`:

```json
{
  "mcpServers": {
    "yanbao": {
      "command": "yanbao",
      "args": ["serve"]
    }
  }
}
```

### 集成到 Claude Desktop

编辑 `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "yanbao": {
      "command": "yanbao",
      "args": ["serve"]
    }
  }
}
```

---

## 数据库结构

单一 SQLite 文件 `data/reports.db`，含 4 张核心表：

### reports — 研报元数据

| 字段 | 类型 | 说明 |
|--------|------|-----------|
| `info_code` | TEXT PK | 研报唯一标识 |
| `title` | TEXT | 标题 |
| `org` | TEXT | 券商名称 |
| `stock` | TEXT | 关联股票名称 |
| `stock_code` | TEXT | 股票代码 |
| `industry` | TEXT | 所属行业 |
| `em_rating` | TEXT | 东方财富评级 |
| `authors` | TEXT | 作者 JSON 数组 |
| `report_date` | TEXT | 发布日期 |
| `report_url` | TEXT | 东方财富页面 URL |
| `pdf_url` | TEXT | PDF 源 URL |
| `pdf_path` | TEXT | 本地 PDF 路径 |
| `json_path` | TEXT | 本地 JSON 路径 |
| `file_hash` | TEXT | JSON 文件 MD5（用于增量检测） |
| `page_count` | INTEGER | PDF 页数（0=待处理，-1=损坏） |
| `indexed_at` | TEXT | 索引时间戳 |

### text_chunks — 文本分块

| 字段 | 类型 | 说明 |
|--------|------|-----------|
| `chunk_id` | INTEGER PK | 块 ID |
| `info_code` | TEXT FK | 关联研报 |
| `chunk_index` | INTEGER | 块序号 |
| `chunk_text` | TEXT | 块文本内容 |
| `token_count` | INTEGER | 字符数 |

### reports_fts — FTS5 全文索引（虚拟表）

对 reports 表的 `title`/`stock`/`industry`/`org` 字段建立全文索引，通过触发器自动同步。

### chunk_vectors — 向量索引（sqlite-vec）

| 字段 | 类型 | 说明 |
|--------|------|-----------|
| `chunk_id` | INTEGER PK | 关联文本块 |
| `embedding` | float[384] | 384 维余弦归一化向量 |

---

## 索引管线

索引执行顺序：

```
JSON 文件扫描 → 元数据入库 → PDF 文本提取 → 文本分块 → 向量生成
```

### 增量索引逻辑

`yanbao index`（无参数）或 `yanbao index --incremental`：

1. 扫描 `YANBAO_DATA_DIR` 所有 `.json` 文件
2. 计算每个 JSON 的 MD5
3. 与数据库中 `file_hash` 对比，找出新增/变更文件
4. 仅处理变更文件：元数据更新 + PDF 提取 + 向量生成

### 断点续传

`yanbao index --full` 支持断点续传：

- `page_count = 0` → 待处理 PDF
- `page_count > 0` → 已处理，跳过
- `page_count = -1` → 损坏 PDF，跳过

重复运行相同命令会自动跳过已完成的阶段。

### 并行加速

对于大量 PDF 提取，使用 `--workers N`：

```bash
yanbao index --full --workers 8
```

架构：主进程将任务分为 4×Worker 个小批次 → `multiprocessing.Pool` 分发 → Worker 只做 PDF 提取（零 DB 操作） → 主进程串行写入 SQLite。

---

## 搜索

### 元数据搜索

支持多条件组合过滤，全部为 AND 关系：

- 关键词 → FTS5 全文索引（`title` / `stock` / `industry` / `org`）
- 股票代码/名称 → 模糊匹配
- 行业 → 模糊匹配
- 券商 → 模糊匹配
- 日期范围 → 范围过滤

### 语义搜索

基于 `BAAI/bge-small-zh-v1.5` 模型将查询和文本块编码为 384 维向量，余弦距离排序。

> 注意：语义搜索需要 GPU/CPU 加载嵌入模型。如果 `sentence-transformers` 依赖缺失或模型加载失败，向量阶段会被跳过，语义搜索返回空结果。

---

## 下载限流策略

`yanbao download` / `yanbao update` 内置自适应速率限制器：

### 滑动窗口

- 窗口长度：2 秒
- 以 QPS（每秒请求数）为上限控制请求频率
- 超过容量则排队等待窗口内最早请求过期

### 指数退避

| 触发条件 | 行为 |
|---------------|--------|
| HTTP 429/403 | `backoff ×= 2`（最大 16 倍） |
| HTTP 5xx | `backoff += 1` |
| 连续 10 次成功 | `backoff /= 2`（恢复升速） |

### 状态文件

`YANBAO_DATA_DIR/.download_state.json` 记录已下载的研报，避免重复下载。每次完成 20 篇或间隔 10 秒自动保存。

---

## 损坏 PDF 处理

索引过程中检测到的损坏 PDF 会被记录到 `data/corrupted_pdfs.jsonl`：

```
{"code": "AP202606010001", "pdf_path": "H:/tmp/reports/中信证券/20260601_xxx.pdf", "error": "EOF marker not found", "timestamp": "2026-06-17T12:30:00"}
```

- 数据库中标记 `page_count = -1`，断点续传跳过
- 重新下载后从 `corrupted_pdfs.jsonl` 移除即可重试
- 下载器会自动跳过已损坏文件

---

## 常见问题

### Q: `yanbao index` 全量索引很慢怎么办？

用并行模式：`yanbao index --full --workers 8`。单线程侧重于小量增量更新，并行模式适合 24000+ 个 PDF 的全量提取。首次全量索引可能需要数小时。

### Q: Ctrl+C 无法退出索引？

最新版本已修复：使用 `apply_async` + 轮询模式 + `pool.terminate()`，Windows 下 Ctrl+C 可正常退出。如果仍卡住，用任务管理器结束 Python 进程。

### Q: pypdf 输出大量 "Ignoring wrong pointing object" 警告？

已在 `indexer.py` 中将 pypdf 日志级别设为 `ERROR`，不会刷屏。这些是 pypdf 对 PDF 内部结构轻微问题的提示，不影响提取。

### Q: 语义搜索返回空？

确认满足以下条件：
1. 已运行 `yanbao index --full` 完成全量索引
2. `sentence-transformers` 和相关依赖已正确安装（`pip install -r requirements.txt`）
3. 索引日志中没有 "Embedding model unavailable, skipping vector generation" 的警告

常见修复：
```bash
pip uninstall scipy sentence-transformers; pip install sentence-transformers
```

### Q: 下载报 HTTP 403/429？

说明触发了东方财富的反爬机制。解决方案：
1. 降低 QPS：`--qps 0.5 --min-qps 0.02`
2. 限制页数：`--max-pages 50`
3. 系统会自动指数退避，等待一段时间后恢复

### Q: 如何重置数据库重新索引？

```bash
yanbao index --full --reset
```

会清空 `reports`、`text_chunks`、`chunk_vectors` 表后重新索引。需要确认操作，可配合 `--workers N` 使用。

### Q: 数据存在哪里？

- 原始研报 JSON + PDF：`YANBAO_DATA_DIR`（默认 `H:/tmp/reports`）
- SQLite 数据库：`data/reports.db`
- 索引状态：`data/.index_state.json`
- 损坏 PDF 记录：`data/corrupted_pdfs.jsonl`
- 下载状态：`YANBAO_DATA_DIR/.download_state.json`
