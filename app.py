from __future__ import annotations

import json
import os
import re
import time
import traceback
from html import unescape
from typing import Any, Callable, TypeVar

import requests
import streamlit as st
import yfinance as yf
from dotenv import load_dotenv

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
            time.sleep(RETRY_SLEEP_SECONDS * (2 ** (attempt - 1)))
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
        }
        for label, keys in mapping.items():
            for k in keys:
                val = info.get(k)
                if val is not None:
                    fundamentals[label] = val
                    break

        return {"prices": prices, "change_pct": change_pct, "fundamentals": fundamentals}

    except Exception as exc:
        print(f"[yfinance] 失败：{exc}")
        return {"prices": [], "change_pct": 0, "fundamentals": {}}


# ── 新闻数据（多源聚合）─────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text.replace("　", " ").replace("\r\n", " ").replace("\n", " ")).strip()


def _fetch_eastmoney(stock_code: str, limit: int) -> list[dict]:
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner = {
        "uid": "", "keyword": stock_code,
        "type": ["cmsArticleWebOld"], "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default", "pageIndex": 1, "pageSize": limit, "preTag": "", "postTag": ""}},
    }
    params = {"cb": "cb", "param": json.dumps(inner, ensure_ascii=False), "_": str(int(time.time() * 1000))}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("cb(") and text.endswith(")"):
        text = text[3:-1]
    rows = json.loads(text).get("result", {}).get("cmsArticleWebOld") or []
    return [{"时间": r.get("date", ""), "来源": r.get("mediaName", ""), "标题": _clean(r.get("title", "")), "内容": _clean(r.get("content", ""))[:200]} for r in rows[:limit]]


def _fetch_sina(stock_code: str, limit: int) -> list[dict]:
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num={limit}&page=1&r=0.1&callback=cb"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("cb(") and text.endswith(")"):
        text = text[3:-1]
    items = json.loads(text).get("result", {}).get("data") or []
    results = []
    for item in items[:limit * 2]:
        title = _clean(item.get("title", ""))
        intro = _clean(item.get("intro", ""))
        if stock_code in title or stock_code in intro:
            results.append({"时间": item.get("ctime", ""), "来源": item.get("media_name", "新浪财经"), "标题": title, "内容": intro[:200]})
    return results[:limit]


def get_news(stock_code: str, limit: int = 5) -> list[dict]:
    all_news: list[dict] = []
    seen: set[str] = set()

    for name, fetcher in [("东方财富", _fetch_eastmoney), ("新浪财经", _fetch_sina)]:
        try:
            items = fetcher(stock_code, limit)
            for item in items:
                if item["标题"] and item["标题"] not in seen:
                    seen.add(item["标题"])
                    all_news.append(item)
        except Exception as exc:
            print(f"[news] {name}失败：{exc}")

    return all_news[:limit]


# ── DeepSeek 分析 ─────────────────────────────────────────

def analyze_with_llm(stock_code: str, stock_data: dict, news: list[dict]) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("请配置环境变量 DEEPSEEK_API_KEY")

    change_pct = stock_data["change_pct"]
    trend = "上涨" if change_pct > 0 else "下跌" if change_pct < 0 else "持平"

    sections = [f"请分析这只 A 股：{stock_code}"]
    if stock_data["prices"]:
        sections.append(f"近 7 天行情：\n{json.dumps(stock_data['prices'], ensure_ascii=False, indent=2)}")
        sections.append(f"涨跌幅：{change_pct}%（{trend}）")
    else:
        sections.append("行情数据：暂无")
    if stock_data["fundamentals"]:
        sections.append(f"基本面：\n{json.dumps(stock_data['fundamentals'], ensure_ascii=False, indent=2)}")
    if news:
        sections.append(f"最新新闻：\n{json.dumps(news, ensure_ascii=False, indent=2)}")
    else:
        sections.append("新闻：暂无")
    sections.append("\n请按下面格式输出（不要用星号、引号等符号）：\n1. 资金情绪：\n2. 新闻重点：\n3. 操作提示：\n\n注意：面向新手解释，不要用复杂金融术语。")

    def _call() -> str:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(sections)}], "temperature": 0.3, "max_tokens": 900},
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        for old, new in [("**", ""), ("##", ""), ("\"", ""), ("`", ""), ("---", "")]:
            text = text.replace(old, new)
        return text.strip()

    return retry_call(_call)


# ── Streamlit UI ──────────────────────────────────────────

st.set_page_config(page_title="A股新手分析助手", layout="centered")
st.title("A股新手分析助手")
st.caption("输入股票代码，自动获取行情、新闻和基本面，用大白话生成分析。")


def run_analysis(raw_code: str) -> str:
    stock_code = normalize_stock_code(raw_code)
    stock_data = get_price_data(stock_code, days=7)
    news = get_news(stock_code, limit=5)
    return analyze_with_llm(stock_code, stock_data, news)


with st.form("stock_form"):
    stock_code_input = st.text_input("股票代码", value="688256", placeholder="例如：688256", max_chars=12)
    submitted = st.form_submit_button("开始分析", type="primary", use_container_width=True)

if submitted:
    with st.spinner("正在获取行情、新闻并调用大模型分析..."):
        try:
            st.session_state["result"] = run_analysis(stock_code_input)
        except Exception as exc:
            st.session_state["result"] = ""
            st.error(f"分析失败：{exc}")

st.divider()
st.subheader("分析结果")
result = st.session_state.get("result")
if result:
    st.markdown(result)
else:
    st.info("点击「开始分析」后，这里会显示大白话分析结果。")
