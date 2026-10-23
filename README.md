# yanbao-mcp

A 股券商研报本地知识库。从东方财富下载研报，建立 SQLite 本地索引，提供 CLI 和 MCP Server 两种使用方式。

## 功能

- **研报下载** — 从东方财富全量/增量下载研报 JSON + PDF，自适应限流 + 指数退避
- **元数据检索** — 支持多条件组合过滤（股票/行业/券商/日期/关键词），FTS5 全文索引
- **语义搜索** — 基于 `BAAI/bge-small-zh-v1.5` 的 512 维向量余弦相似度搜索
- **因子提取** — 从研报文本中提取结构化驱动因子（产能/订单/报价/增速/政策/成本/库存/销量），支持规则提取和 LLM 提取
- **RAG 问答** — LLM 驱动的研报问答、单篇摘要、多研报综合分析（兼容 OpenAI API）
- **MCP Server** — 通过 Model Context Protocol 与 opencode、Claude Desktop 等 AI 客户端集成
- **损坏 PDF 自动修复** — 检测并重新下载损坏 PDF，断点续传

## 快速开始

```bash
# 安装
pip install -r requirements.txt
pip install -e .

# 配置（可选，有默认值）
# 编辑 .env 设置 YANBAO_DATA_DIR、YANBAO_DB_PATH 等

# 下载研报（快速测试）
yanbao download --qps 2.0 --max-pages 5

# 全量索引
yanbao index --full --workers 8

# 搜索
yanbao search --keyword "半导体"

# 启动 MCP Server
yanbao serve
```

## 架构

```
东方财富 API
    ↓ 下载 (YanbaoDownloader)
研报 JSON + PDF  (YANBAO_DATA_DIR)
    ↓ 索引 (yanbao index)
SQLite 数据库 (data/reports.db)
 ├── reports          — 元数据 (FTS5 全文索引)
 ├── text_chunks      — PDF 文本分块
 ├── chunk_vectors    — 文本向量 (sqlite-vec, 512维)
 ├── factors          — 结构化因子
 ├── factor_chunks    — 因子文本块
 └── factor_vectors   — 因子向量 (sqlite-vec, 512维)
    ↓ 查询
CLI (click) / MCP Server (mcp)
```

## 核心依赖

| 包 | 用途 |
|------|---------|
| `click` | CLI 框架 |
| `mcp` | MCP 协议服务端 |
| `pypdf` | PDF 文本提取 |
| `sentence-transformers` | 向量嵌入模型 |
| `sqlite-vec` | SQLite 向量索引 |
| `httpx` | LLM API 客户端 |
| `numpy` | 向量计算 |

## 详细文档

详见 [USAGE.md](USAGE.md)，包含：

- 命令行速查表见 [CLI.md](CLI.md)
- 完整配置项说明
- 所有 CLI 命令详解（download/update/index/search/semantic/report/by-stock/by-industry/status/stats/serve/fix/chat/summary/analyze）
- MCP Server 工具列表及客户端集成指南
- 数据库结构
- 索引管线（增量/全量/并行/断点续传）
- 因子提取说明
- 下载限流策略
- 常见问题

## 项目结构

```
yanbao-mcp/
├── yanbao/
│   ├── cli.py            — CLI 入口 (click)
│   ├── mcp_server.py     — MCP Server
│   ├── downloader.py     — 东方财富研报下载器
│   ├── scanner.py        — JSON 文件扫描
│   ├── extractor.py      — PDF 文本提取
│   ├── indexer.py        — 索引管线编排
│   ├── search.py         — 搜索逻辑
│   ├── embeddings.py     — 向量编码
│   ├── factor_extractor.py — 因子提取
│   ├── llm.py            — LLM 客户端 (RAG/摘要/分析)
│   ├── db.py             — 数据库初始化与查询
│   └── config.py         — 配置管理
├── tests/                — 单元测试
├── CLI.md                — 命令行速查表
├── USAGE.md              — 完整使用手册
└── pyproject.toml        — 项目元数据
```
