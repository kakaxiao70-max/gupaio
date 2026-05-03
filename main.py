from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from html import unescape
from typing import Any, Callable, TypeVar

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(".env", encoding="utf-8")

# ── LLM 配置 ──────────────────────────────────────────────

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


# ── 股票代码工具 ──────────────────────────────────────────

def normalize_stock_code(raw_code: str) -> str:
    code = raw_code.strip().upper()
    code = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
    code = code.replace(".", "")
    if not (code.isdigit() and len(code) == 6):
        raise ValueError("股票代码应为 6 位数字，例如：688256")
    return code


def to_yfinance_ticker(code: str) -> str:
    """6 位 A 股代码 → yfinance ticker（688256 → 688256.SS）"""
    if code.startswith(("60", "68")):
        return f"{code}.SS"
    if code.startswith(("00", "30")):
        return f"{code}.SZ"
    raise ValueError(f"不支持的 A 股代码前缀：{code}")


# ── yfinance 获取行情 ─────────────────────────────────────

def get_recent_history(stock_code: str, days: int = 5) -> pd.DataFrame:
    ticker = to_yfinance_ticker(stock_code)
    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    df = yf.download(
        ticker,
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
    )

    if df.empty:
        return pd.DataFrame()

    # yfinance 返回的列可能是 MultiIndex，压平
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.tail(days).reset_index()
    df = df.rename(columns={
        "Date": "日期",
        "Open": "开盘",
        "Close": "收盘",
        "High": "最高",
        "Low": "最低",
        "Volume": "成交量",
    })
    return df[["日期", "开盘", "收盘", "最高", "最低", "成交量"]]


# ── 东方财富新闻 ──────────────────────────────────────────

def _clean_news_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for old, new in (
        ("<em>", ""), ("</em>", ""), ("(<em>", ""), ("</em>)", ""),
        ("　", ""), ("\r\n", " "), ("\n", " "),
    ):
        text = text.replace(old, new)
    return unescape(text).strip()


def get_latest_news(stock_code: str, limit: int = 5) -> pd.DataFrame:
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    def _fetch() -> pd.DataFrame:
        resp = requests.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data_text = resp.text.strip()
        prefix = f"{callback}("
        if data_text.startswith(prefix) and data_text.endswith(")"):
            data_text = data_text[len(prefix):-1]
        data_json = json.loads(data_text)
        rows = data_json.get("result", {}).get("cmsArticleWebOld") or []
        records = []
        for row in rows[:limit]:
            records.append({
                "发布时间": row.get("date"),
                "文章来源": row.get("mediaName"),
                "新闻标题": _clean_news_text(row.get("title")),
                "新闻内容": _clean_news_text(row.get("content")),
            })
        return pd.DataFrame(records)

    return retry_call(_fetch)


# ── 大模型分析 ────────────────────────────────────────────

def dataframe_to_records_text(df: pd.DataFrame) -> str:
    if df.empty:
        return "[]"
    records = df.where(pd.notna(df), None).to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False, indent=2, default=str)


def analyze_stock_with_llm(
    stock_code: str,
    history_df: pd.DataFrame,
    news_df: pd.DataFrame,
    provider: str = "deepseek",
) -> str:
    providers = {
        "deepseek": {"base_url": "https://api.deepseek.com", "default_model": "deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY"},
        "openai": {"base_url": "https://api.openai.com/v1", "default_model": "gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY"},
    }
    if provider not in providers:
        raise ValueError(f"provider 只支持：{'、'.join(providers)}")

    config = providers[provider]
    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        raise RuntimeError(f"请配置环境变量 {config['api_key_env']}")

    history_text = dataframe_to_records_text(history_df)
    news_text = dataframe_to_records_text(news_df)
    user_prompt = f"""请分析这只 A 股股票的数据，股票代码：{stock_code}

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
- 这不是投资建议，只是数据解读。"""

    payload = {
        "model": config["default_model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 900,
    }

    def _call() -> str:
        url = f"{config['base_url']}/chat/completions"
        print(f"[LLM] POST {url}")
        print(f"[LLM] api_key set: {bool(api_key)}, prefix: {api_key[:8] if api_key else 'N/A'}...")
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        print(f"[LLM] status: {resp.status_code}, body[:200]: {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    return retry_call(_call)


# ── FastAPI ───────────────────────────────────────────────

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AnalyzeRequest(BaseModel):
    stock_code: str
    provider: str = "deepseek"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    try:
        stock_code = normalize_stock_code(req.stock_code)
        print(f"[analyze] stock_code={stock_code}, provider={req.provider}")

        history_df = get_recent_history(stock_code, days=5)
        print(f"[analyze] history rows: {len(history_df)}")

        news_df = get_latest_news(stock_code, limit=5)
        print(f"[analyze] news rows: {len(news_df)}")

        analysis = analyze_stock_with_llm(stock_code, history_df, news_df, provider=req.provider)
        return {"ok": True, "stock_code": stock_code, "analysis": analysis}
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
