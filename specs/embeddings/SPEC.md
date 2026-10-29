---
id: embeddings
type: module-design
title: 向量编码
status: draft
parent: architecture
depends-on:
  - configuration
---

# 向量编码

## 责任

`yanbao/embeddings.py`：加载嵌入模型，把文本编码成 512 维归一化向量；模型不可用时优雅降级。

## 边界

只依赖配置（模型名与维度）。**不知道数据库与检索**：它接收文本、交出向量，向量往哪写由 [indexing] 与 [retrieval] 决定。

## 决策与不变量

- **惰性加载、全局单例**：模型在第一次真正需要时才加载，整个进程共用一份；调用方通过 `is_available()` 判断能否使用向量能力。
- **本地缓存优先**：先以 `local_files_only` 从缓存加载，未命中再联网下载——离线环境也能用。
- **归一化 + 余弦**：编码时做 L2 归一化，距离度量用余弦，与库中 vec0 表的定义一致。
- **失败即禁用而非崩溃**：依赖缺失或模型加载失败时，`is_available()` 返回 False，由调用方决定跳过（索引阶段）或报错（语义检索）；本模块不向上抛异常。
- **维度必须与配置一致**：输出维度固定 512，与 `bge-small-zh-v1.5` 的 hidden_size 对齐。换模型必须同时改配置与库表定义并重建索引。
- **Windows 证书库补丁**：部分 Windows 机器的系统证书库损坏会让 `ssl.load_default_certs` 抛错，进而导致 sentence-transformers 整条导入链失败。模块加载时对该调用打补丁，降级为只使用 `SSL_CERT_FILE` 指定的 CA。

## 相关

块长度与 token 上限的对应关系见 [extraction]；模型名与维度配置项见 [configuration]。
