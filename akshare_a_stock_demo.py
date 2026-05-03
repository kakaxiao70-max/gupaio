"""
AKShare A股数据示例：
1. 输入股票代码，例如 688256
2. 获取最近 5 个交易日历史行情
3. 获取最新几条个股新闻
4. 遇到网络或接口异常时自动重试
"""

from __future__ import annotations

import time
import json
import os
from html import unescape
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, TypeVar

import akshare as ak
import pandas as pd
import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8")

MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 2
SYSTEM_PROMPT = (
    "你是一个资深的 A 股分析师。请用大白话为毫无金融基础的新手总结以下数据。"
    "1. 总结目前的资金情绪是乐观还是悲观；"
    "2. 提炼新闻里的核心利好/利空因素；"
    "3. 给出简单的操作提示（如：多看少动、保持观望等），绝对不要使用复杂的金融术语。"
)

T = TypeVar("T")


def retry_call(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """调用接口，失败后按 2、4、8 秒退避重试。"""
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break

            sleep_seconds = RETRY_SLEEP_SECONDS * (2 ** (attempt - 1))
            print(f"接口调用失败，第 {attempt} 次重试前等待 {sleep_seconds} 秒：{exc}")
            time.sleep(sleep_seconds)

    raise RuntimeError(f"接口连续失败 {MAX_RETRIES} 次，请稍后再试。最后错误：{last_error}")


def normalize_stock_code(raw_code: str) -> str:
    """把 sh688256、688256.SH 等输入统一成 AKShare 常用的 6 位数字代码。"""
    code = raw_code.strip().upper()
    code = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
    code = code.replace(".", "")

    if not (code.isdigit() and len(code) == 6):
        raise ValueError("股票代码应为 6 位数字，例如：688256")

    return code


def get_recent_history(stock_code: str, days: int = 5) -> pd.DataFrame:
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    history_df = retry_call(
        ak.stock_zh_a_hist,
        symbol=stock_code,
        period="daily",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust="",
    )

    if history_df.empty:
        return history_df

    return history_df.tail(days)


def _clean_news_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for old, new in (
        ("<em>", ""),
        ("</em>", ""),
        ("(<em>", ""),
        ("</em>)", ""),
        ("\u3000", ""),
        ("\r\n", " "),
        ("\n", " "),
    ):
        text = text.replace(old, new)
    return unescape(text).strip()


def _fetch_eastmoney_news(stock_code: str, limit: int) -> pd.DataFrame:
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    callback = "callback"
    inner_param = {
        "uid": "",
        "keyword": stock_code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": limit,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    params = {
        "cb": callback,
        "param": json.dumps(inner_param, ensure_ascii=False),
        "_": str(int(time.time() * 1000)),
    }
    headers = {
        "Referer": f"https://so.eastmoney.com/news/s?keyword={stock_code}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    }

    response = requests.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    data_text = response.text.strip()
    prefix = f"{callback}("
    if data_text.startswith(prefix) and data_text.endswith(")"):
        data_text = data_text[len(prefix) : -1]

    data_json = json.loads(data_text)
    rows = data_json.get("result", {}).get("cmsArticleWebOld") or []
    records = []
    for row in rows[:limit]:
        title = _clean_news_text(row.get("title"))
        content = _clean_news_text(row.get("content"))
        code = row.get("code") or ""
        records.append(
            {
                "发布时间": row.get("date"),
                "文章来源": row.get("mediaName"),
                "新闻标题": title,
                "新闻内容": content,
                "新闻链接": f"http://finance.eastmoney.com/a/{code}.html" if code else None,
            }
        )

    return pd.DataFrame(
        records,
        columns=["发布时间", "文章来源", "新闻标题", "新闻内容", "新闻链接"],
    )


def get_latest_news(stock_code: str, limit: int = 5) -> pd.DataFrame:
    news_df = retry_call(_fetch_eastmoney_news, stock_code, limit)

    if news_df.empty:
        return news_df

    expected_columns = ["发布时间", "文章来源", "新闻标题", "新闻内容", "新闻链接"]
    existing_columns = [column for column in expected_columns if column in news_df.columns]
    return news_df.loc[:, existing_columns].head(limit)


def dataframe_to_records_text(df: pd.DataFrame) -> str:
    """把 DataFrame 转成适合放进 Prompt 的 JSON 文本。"""
    if df.empty:
        return "[]"

    records = df.where(pd.notna(df), None).to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False, indent=2, default=str)


def build_stock_analysis_prompt(
    stock_code: str,
    history_df: pd.DataFrame,
    news_df: pd.DataFrame,
) -> str:
    """把股票代码、最近行情和新闻打包成给大模型看的用户 Prompt。"""
    history_text = dataframe_to_records_text(history_df)
    news_text = dataframe_to_records_text(news_df)

    return f"""
请分析这只 A 股股票的数据，股票代码：{stock_code}

最近 5 个交易日价格数据：
{history_text}

最新新闻数据：
{news_text}

请按下面格式输出：
1. 资金情绪：
2. 新闻重点：
3. 操作提示：

注意：
- 只基于我提供的数据分析，不要编造没有出现的信息。
- 面向新手解释，要像给朋友讲一样简单。
- 这不是投资建议，只是数据解读。
""".strip()


def analyze_stock_with_llm(
    stock_code: str,
    history_df: pd.DataFrame,
    news_df: pd.DataFrame,
    provider: str = "deepseek",
    model: str | None = None,
    api_key: str | None = None,
    timeout: int = 60,
) -> str:
    """
    调用 DeepSeek 或 OpenAI，让大模型总结股票行情和新闻。

    使用方式：
    - DeepSeek：先设置环境变量 DEEPSEEK_API_KEY
    - OpenAI：先设置环境变量 OPENAI_API_KEY，并把 provider 改成 "openai"
    """
    providers = {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "default_model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",

        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4.1-mini",
            "api_key_env": "OPENAI_API_KEY",

        },
    }

    if provider not in providers:
        supported = "、".join(providers)
        raise ValueError(f"provider 只支持：{supported}")

    config = providers[provider]
    final_api_key = api_key or os.getenv(config["api_key_env"])
    if not final_api_key:
        raise RuntimeError(
            f"请先在项目根目录的 {PROJECT_ROOT / '.env'} 文件里配置 {config['api_key_env']}，"
            "或通过 api_key 参数传入。"
        )

    user_prompt = build_stock_analysis_prompt(stock_code, history_df, news_df)
    payload = {
        "model": model or config["default_model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 900,
    }

    def request_llm() -> str:
        response = requests.post(
            f"{config['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {final_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]

    return retry_call(request_llm)


def main() -> None:
    raw_code = input("请输入 A 股股票代码（默认 688256）：").strip() or "688256"
    stock_code = normalize_stock_code(raw_code)

    print(f"\n正在获取 {stock_code} 最近 5 个交易日历史行情...")
    history_df = get_recent_history(stock_code)
    if history_df.empty:
        print("没有获取到历史行情数据。")
    else:
        print(history_df.to_string(index=False))

    print(f"\n正在获取 {stock_code} 最新新闻...")
    news_df = get_latest_news(stock_code)
    if news_df.empty:
        print("没有获取到新闻数据。")
    else:
        print(news_df.to_string(index=False))

    should_analyze = input("\n是否调用大模型分析？输入 y 继续，其他键跳过：").strip().lower()
    if should_analyze == "y":
        provider = input("请选择 provider（deepseek/openai，默认 deepseek）：").strip().lower() or "deepseek"
        print("\n正在调用大模型分析...")
        analysis = analyze_stock_with_llm(stock_code, history_df, news_df, provider=provider)
        print("\n大模型分析结果：")
        print(analysis)


if __name__ == "__main__":
    main()
