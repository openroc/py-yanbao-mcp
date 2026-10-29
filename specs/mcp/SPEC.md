---
id: mcp
type: module-design
title: MCP Server 工具面
status: draft
parent: architecture
depends-on:
  - retrieval
  - llm
  - storage
---

# MCP Server 工具面

## 责任

`yanbao/mcp_server.py`：把检索与问答能力以 MCP 工具暴露给 AI 客户端，通过标准输入/输出（stdio）与客户端通信。

## 边界

只调用检索、LLM 与存储统计。**不含业务逻辑、不写库**：工具实现只是把参数转给对应能力，再把结果格式化成模型可直接消费的文本。

## 决策与不变量

- **工具面与 CLI 对齐**：MCP 暴露的检索能力应当与 CLI 一一对应。目前 MCP 已包含因子检索三件套，而 CLI 尚未提供对应命令（见 [cli-surface] 的已知缺口）。
- **每次调用独立开连接**：连接在调用开始时创建、结束时关闭，不跨调用共享。
- **输出是纯文本**：报告列表、语义结果、单篇详情与因子列表都渲染成带编号与字段的文本块，便于模型直接引用。
- **失败要能被模型读懂**：未启用 LLM、LLM 调用失败等情形返回可读的中文说明，而不是抛出协议层错误。
- **工具集合是稳定契约**：工具名与参数是客户端可见的契约，新增或改名属于破坏性变更。

## 相关

客户端配置示例见 USAGE.md 的「MCP Server」章节。
