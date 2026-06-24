"""
因子提取模块 - 从研报文本中提取结构化驱动因子

因子类型: 产能/订单/报价/增速/政策/成本/库存/销量
"""

import json
import logging
from typing import Any

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL, LLM_TIMEOUT

logger = logging.getLogger(__name__)


# LLM 因子提取 Prompt
FACTOR_EXTRACT_PROMPT = """你是一个专业的研报因子分析师。从以下研报文本中提取核心驱动因子。

输出格式要求：
- 必须输出有效的JSON数组
- 每条因子包含: type, factor, value, unit, context
- 只提取有具体数值的因子
- 数值只保留数字，删除逗号（如"1,200" -> 1200）
- context 是原文句子，保留完整上下文

因子类型定义：
- 产能: 产能规模(万吨/年, GW等)、利用率(%)、扩产计划
- 订单: 订单量(亿元)、交付周期、在手订单
- 报价: 产品单价(万元/吨, 元/瓦等)
- 增速: 同比/环比增长率(%)，标注YoY或QoQ
- 政策: 政策文件、补贴标准、规划目标
- 成本: 单位成本、毛利率、净利率(%)
- 库存: 库存量(万吨)、周转天数(天)
- 销量: 销量(万辆, GWh等)、市占率(%)

研报文本：
{text}

输出JSON数组："""


FACTOR_TYPES = [
    "产能", "订单", "报价", "增速",
    "政策", "成本", "库存", "销量"
]


def _parse_number(s: str) -> float | None:
    """从字符串中解析数字"""
    if not s:
        return None
    # 删除逗号和空格
    s = s.replace(",", "").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _extract_factors_from_json(json_str: str, chunk_text: str) -> list[dict[str, Any]]:
    """解析 LLM 返回的 JSON，转换为因子列表"""
    factors = []
    try:
        # 尝试解析 JSON
        data = json.loads(json_str)
        if not isinstance(data, list):
            data = [data]

        for item in data:
            if not isinstance(item, dict):
                continue

            ftype = item.get("type", "")
            if ftype not in FACTOR_TYPES:
                continue

            value = item.get("value")
            if value is None:
                continue

            # 转换数值为 float
            if isinstance(value, str):
                value = _parse_number(value)
            if value is None:
                continue

            factor = {
                "factor_type": ftype,
                "factor_name": str(item.get("factor", "")),
                "value": float(value),
                "unit": str(item.get("unit", "")),
                "source_text": str(item.get("context", ""))[:500],
                "chunk_text": chunk_text[:2000],
            }
            factors.append(factor)

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed: {e}")
    return factors


def extract_factors_from_text(text: str) -> list[dict[str, Any]]:
    """
    直接从文本提取因子（用于测试或无 LLM 场景）
    基于规则的简单提取
    """
    import re

    factors = []
    lines = text.split("\n")

    unit_patterns = [
        (r"([\d,.]+)\s*[万亿元]/[年吨个]?", "万/单位", "产能"),
        (r"([\d,.]+)\s*%.*?(利用率|产能)", "%", "产能"),
        (r"([\d,.]+)\s*亿.*?(订单|在手)", "亿", "订单"),
        (r"([\d,.]+)\s*[万亿]?元/[吨瓦公斤]", "元/单位", "报价"),
        (r"([\d,.]+)\s*%.*?(同比|YoY|增长)", "%", "增速"),
        (r"([\d,.]+)\s*%.*?(毛利|净利|利率)", "%", "成本"),
        (r"([\d,.]+)\s*[万辆万吨万台GWh]", "单位", "销量"),
        (r"([\d,.]+)\s*天", "天", "库存"),
    ]

    for line in lines:
        for pattern, unit, ftype in unit_patterns:
            matches = re.findall(pattern, line)
            for m in matches:
                num_str = m if isinstance(m, str) else m[0]
                value = _parse_number(num_str)
                if value and value < 1e10:  # 过滤异常大的数字
                    factors.append({
                        "factor_type": ftype,
                        "factor_name": f"{ftype}相关",
                        "value": value,
                        "unit": unit,
                        "source_text": line.strip()[:500],
                        "chunk_text": text[:2000],
                    })

    return factors


def extract_factors_with_llm(text: str, llm_client=None) -> list[dict[str, Any]]:
    """
    使用 LLM 提取因子

    Args:
        text: 研报文本块
        llm_client: 可选的 LLM 客户端实例

    Returns:
        因子列表
    """
    if not LLM_ENABLED:
        logger.warning("LLM not enabled, using rule-based extraction")
        return extract_factors_from_text(text)

    # 构建 prompt
    prompt = FACTOR_EXTRACT_PROMPT.format(text=text[:8000])

    try:
        if llm_client is None:
            from .llm import LLMClient
            llm_client = LLMClient()

        response = llm_client.chat([
            {"role": "user", "content": prompt}
        ])

        return _extract_factors_from_json(response, text)

    except Exception as e:
        logger.warning(f"LLM factor extraction failed: {e}, falling back to rules")
        return extract_factors_from_text(text)


def batch_extract_factors(texts: list[str], llm_client=None) -> list[list[dict[str, Any]]]:
    """
    批量提取因子

    Args:
        texts: 文本块列表
        llm_client: 可选的 LLM 客户端

    Returns:
        每个文本块对应的因子列表
    """
    results = []
    for text in texts:
        factors = extract_factors_with_llm(text, llm_client)
        results.append(factors)
    return results
