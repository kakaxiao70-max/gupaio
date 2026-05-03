from __future__ import annotations

import json
import os
import re
import time
import traceback
from html import unescape
from typing import Any, Callable, TypeVar

import feedparser
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(".env", encoding="utf-8")

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
            secs = RETRY_SLEEP_SECONDS * (2 ** (attempt - 1))
            print(f"[retry] {attempt}/{MAX_RETRIES}，等待 {secs}s：{exc}")
            time.sleep(secs)
    raise RuntimeError(f"连续失败 {MAX_RETRIES} 次：{last_error}")


# ── 股票代码 ──────────────────────────────────────────────

def normalize_stock_code(raw_code: str) -> str:
    code = raw_code.strip().upper()
    for s in ("SH", "SZ", "BJ"):
        code = code.replace(s, "")
    code = code.replace(".", "")
    if not (code.isdigit() and len(code) == 6):
        raise ValueError("股票代码应为 6 位数字，例如：688256")
    return code


def to_yfinance_ticker(code: str) -> str:
    if code.startswith(("60", "68")):
        return f"{code}.SS"
    if code.startswith(("00", "30")):
        return f"{code}.SZ"
    raise ValueError(f"不支持的 A 股代码前缀：{code}")


# ── 价格数据（yfinance）──────────────────────────────────

def get_price_data(stock_code: str, days: int = 7) -> dict[str, Any]:
    """通过 yfinance 获取行情 + 基本面，失败时返回空结构而非抛异常。"""
    try:
        ticker_symbol = to_yfinance_ticker(stock_code)
        ticker = yf.Ticker(ticker_symbol)

        hist = ticker.history(period=f"{days + 10}d")
        if hist.empty:
            raise ValueError(f"{ticker_symbol} 无行情数据")

        hist = hist.tail(days).reset_index()
        hist["Date"] = hist["Date"].dt.strftime("%Y-%m-%d")

        first_close = hist["Close"].iloc[0]
        last_close = hist["Close"].iloc[-1]
        change_pct = round((last_close - first_close) / first_close * 100, 2)

        prices = []
        for _, row in hist.iterrows():
            prices.append({
                "日期": row["Date"],
                "开盘": round(row["Open"], 2),
                "收盘": round(row["Close"], 2),
                "最高": round(row["High"], 2),
                "最低": round(row["Low"], 2),
                "成交量": int(row["Volume"]),
            })

        info = ticker.info
        fundamentals = {}
        mapping = {
            "名称": ["shortName", "longName"],
            "行业": ["industry"],
            "板块": ["sector"],
            "市值": ["marketCap"],
            "市盈率(TTM)": ["trailingPE"],
            "市净率": ["priceToBook"],
            "52周最高": ["fiftyTwoWeekHigh"],
            "52周最低": ["fiftyTwoWeekLow"],
            "成交量均价": ["averageVolume"],
        }
        for label, keys in mapping.items():
            for k in keys:
                val = info.get(k)
                if val is not None:
                    fundamentals[label] = val
                    break

        return {"prices": prices, "change_pct": change_pct, "fundamentals": fundamentals}

    except Exception as exc:
        print(f"[yfinance] 获取行情失败：{exc}")
        return {"prices": [], "change_pct": 0, "fundamentals": {}}


# ── 新闻数据（RSS 多源聚合）─────────────────────────────

RSS_SOURCES = [
    {
        "name": "新浪财经",
        "url": "https://finance.sina.com.cn/roll/index.d.html?cid=56592",
        "type": "web",
    },
    {
        "name": "东方财富-个股",
        "url": "https://search-api-web.eastmoney.com/search/jsonp",
        "type": "eastmoney",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("　", " ").replace("\r\n", " ").replace("\n", " ")
    return unescape(text).strip()


def _fetch_rss_sina(stock_code: str, limit: int) -> list[dict]:
    """从新浪财经滚动新闻页面抓取标题。"""
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num={limit}&page=1&r=0.1&callback=cb"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("cb(") and text.endswith(")"):
        text = text[3:-1]
    data = json.loads(text)
    items = data.get("result", {}).get("data") or []
    results = []
    for item in items[:limit]:
        title = _clean(item.get("title", ""))
        intro = _clean(item.get("intro", ""))
        if stock_code in title or stock_code in intro or _keyword_match(stock_code, title + intro):
            results.append({
                "时间": item.get("ctime", ""),
                "来源": item.get("media_name", "新浪财经"),
                "标题": title,
                "内容": intro[:200],
            })
    return results


def _fetch_eastmoney(stock_code: str, limit: int) -> list[dict]:
    """从东方财富搜索接口获取个股新闻。"""
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner = {
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
                "preTag": "",
                "postTag": "",
            }
        },
    }
    params = {"cb": "cb", "param": json.dumps(inner, ensure_ascii=False), "_": str(int(time.time() * 1000))}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("cb(") and text.endswith(")"):
        text = text[3:-1]
    rows = json.loads(text).get("result", {}).get("cmsArticleWebOld") or []
    return [
        {"时间": r.get("date", ""), "来源": r.get("mediaName", ""), "标题": _clean(r.get("title", "")), "内容": _clean(r.get("content", ""))[:200]}
        for r in rows[:limit]
    ]


def _keyword_match(stock_code: str, text: str) -> bool:
    """简单关键词匹配，判断新闻是否与该股票相关。"""
    keywords = [stock_code]
    # 常见股票简称关键词（可扩展）
    return any(kw in text for kw in keywords if len(kw) >= 2)


def get_news(stock_code: str, limit: int = 5) -> list[dict]:
    """多源聚合获取新闻，任一源失败不影响其他源。"""
    all_news: list[dict] = []
    seen_titles: set[str] = set()

    # 源 1：东方财富（个股精准匹配）
    try:
        east_news = _fetch_eastmoney(stock_code, limit)
        for item in east_news:
            title = item["标题"]
            if title and title not in seen_titles:
                seen_titles.add(title)
                all_news.append(item)
        print(f"[news] 东方财富：获取 {len(east_news)} 条")
    except Exception as exc:
        print(f"[news] 东方财富失败：{exc}")

    # 源 2：新浪财经（补充宏观情绪）
    try:
        sina_news = _fetch_rss_sina(stock_code, limit)
        for item in sina_news:
            title = item["标题"]
            if title and title not in seen_titles:
                seen_titles.add(title)
                all_news.append(item)
        print(f"[news] 新浪财经：获取 {len(sina_news)} 条")
    except Exception as exc:
        print(f"[news] 新浪财经失败：{exc}")

    if not all_news:
        print(f"[news] 所有新闻源均失败，将仅用行情数据进行分析")

    return all_news[:limit]


# ── DeepSeek 分析 ─────────────────────────────────────────

def analyze_with_llm(stock_code: str, stock_data: dict, news: list[dict]) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("请配置环境变量 DEEPSEEK_API_KEY")

    change_pct = stock_data["change_pct"]
    trend = "上涨" if change_pct > 0 else "下跌" if change_pct < 0 else "持平"

    # 根据可用数据动态构建 prompt
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

    user_prompt = "\n\n".join(sections)

    def _call() -> str:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 900,
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        for old, new in [("**", ""), ("##", ""), ("\"", ""), ("`", ""), ("---", "")]:
            text = text.replace(old, new)
        return text.strip()

    return retry_call(_call)


# ── FastAPI ───────────────────────────────────────────────

ALLOWED_ORIGINS = [
    "https://chunqiao.xo.je",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    stock_code: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    try:
        code = normalize_stock_code(req.stock_code)
        print(f"[analyze] 开始分析 {code}")

        # 1. 获取价格数据（容错）
        stock_data = get_price_data(code, days=7)
        print(f"[analyze] 价格数据：{len(stock_data['prices'])} 天，涨跌幅 {stock_data['change_pct']}%")

        # 2. 获取新闻（多源聚合，容错）
        news = get_news(code, limit=5)
        print(f"[analyze] 新闻：{len(news)} 条")

        # 3. 调用 LLM 分析
        result = analyze_with_llm(code, stock_data, news)
        return {"ok": True, "stock_code": code, "analysis": result}

    except Exception as exc:
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
