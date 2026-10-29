---
id: storage
type: module-design
title: 本地库结构与访问
status: draft
parent: architecture
depends-on:
  - configuration
---

# 本地库结构与访问

## 责任

`yanbao/db.py`：唯一的数据访问入口——连接管理、表结构初始化、统计口径。

## 边界

允许依赖配置。**库表结构由本模块拥有，但 SQL 语句由使用方模块自行编写**（检索、索引各自写查询），本模块只提供连接与建表。

## 决策与不变量

- **单文件本地库**：`data/reports.db`，WAL 日志模式，开启外键约束，启动时加载 sqlite-vec 扩展。七张表的字段契约见 USAGE.md 的「数据库结构」章节。
- **元数据全文索引只覆盖四个字段**：`reports_fts` 是 external-content FTS5 表，只索引 `title`/`stock`/`industry`/`org`，靠触发器与 `reports` 同步。研报正文不进全文索引——正文走向量检索。
- **向量表是 vec0 虚拟表**：`chunk_vectors` 与 `factor_vectors` 的主键分别是 `chunk_id` 与 `factor_id`，距离度量为余弦，维度固定 512。
- **虚表不可与普通表 JOIN**：sqlite-vec 的向量匹配是逐行 KNN，几十万行 JOIN 会跑几十分钟。因此本模块提供「取全部已有向量 id」的扫描接口，由调用方在 Python 侧比对，而不是写 JOIN。
- **没有迁移框架**：库表结构的变更通过 `init_db` / `init_factor_tables` 的幂等建表完成；结构不兼容的改动需要重建库。

## 相关

表的使用方式（写入时机、幂等要求）见 [indexing]；查询语义见 [retrieval]。
