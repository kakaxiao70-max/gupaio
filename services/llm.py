from __future__ import annotations

import json

import requests

from core.config import SYSTEM_PROMPT, get_deepseek_api_key
from core.utils import retry_call

MARKDOWN_CHARS = [("**", ""), ("##", ""), ("\"", ""), ("`", ""), ("---", "")]


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """通用 DeepSeek 调用，自动清理 markdown 格式。"""
    api_key = get_deepseek_api_key()

    def _call() -> str:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 900,
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        for old, new in MARKDOWN_CHARS:
            text = text.replace(old, new)
        return text.strip()

    return retry_call(_call)


def analyze_stock(stock_code: str, stock_data: dict, news: list[dict]) -> str:
    """构建股票分析 prompt 并调用 LLM。"""
    change_pct = stock_data["change_pct"]
    trend = "上涨" if change_pct > 0 else "下跌" if change_pct < 0 else "持平"

    sections = [f"请分析这只 A 股：{stock_code}"]
    if stock_data["prices"]:
        sections.append(f"近 7 天行情数据：\n{json.dumps(stock_data['prices'], ensure_ascii=False, indent=2)}")
        sections.append(f"涨跌幅：{change_pct}%（{trend}）")
    else:
        sections.append("行情数据：暂无（数据源不可用）")
    if stock_data["fundamentals"]:
        sections.append(f"基本面信息：\n{json.dumps(stock_data['fundamentals'], ensure_ascii=False, indent=2)}")
    if news:
        sections.append(f"最新新闻：\n{json.dumps(news, ensure_ascii=False, indent=2)}")
    else:
        sections.append("新闻：暂无（数据源不可用）")
    sections.append(
        "\n请按下面格式输出（不要用星号、引号等符号）：\n"
        "1. 资金情绪：\n2. 新闻重点：\n3. 操作提示：\n\n"
        "注意：面向新手解释，像给朋友讲一样简单，不要用复杂金融术语。"
    )

    return call_llm(SYSTEM_PROMPT, "\n\n".join(sections))
