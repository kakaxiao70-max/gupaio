from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(".env", encoding="utf-8")

from core.utils import normalize_stock_code, to_yfinance_ticker
from routers.predict import _calc_technicals
from services.llm import analyze_stock, call_llm
from services.news import get_news
from services.price import get_price_data
from routers.predict import PREDICT_SYSTEM_PROMPT

st.set_page_config(page_title="A股新手分析助手", layout="centered")
st.title("A股新手分析助手")
st.caption("输入股票代码，获取行情分析和走势预测。")

tab1, tab2 = st.tabs(["基本面分析", "走势预测"])

# ── Tab 1: 基本面分析 ─────────────────────────────────────

with tab1:
    with st.form("analyze_form"):
        code1 = st.text_input("股票代码", placeholder="例如：688256", max_chars=12, key="analyze_code")
        submitted1 = st.form_submit_button("开始分析", type="primary", use_container_width=True)

    if submitted1 and code1:
        with st.spinner("正在获取行情、新闻并调用大模型分析..."):
            try:
                stock_code = normalize_stock_code(code1)
                stock_data = get_price_data(stock_code, days=7)
                news = get_news(stock_code, limit=5)
                st.session_state["analysis"] = analyze_stock(stock_code, stock_data, news)
            except Exception as exc:
                st.session_state["analysis"] = ""
                st.error(f"分析失败：{exc}")

    result1 = st.session_state.get("analysis")
    if result1:
        st.markdown(result1)
    else:
        st.info("输入股票代码后点击「开始分析」。")

# ── Tab 2: 走势预测 ──────────────────────────────────────

with tab2:
    with st.form("predict_form"):
        code2 = st.text_input("股票代码", placeholder="例如：688256", max_chars=12, key="predict_code")
        submitted2 = st.form_submit_button("预测走势", type="primary", use_container_width=True)

    if submitted2 and code2:
        with st.spinner("正在计算技术指标并预测走势..."):
            try:
                stock_code = normalize_stock_code(code2)
                ticker_symbol = to_yfinance_ticker(stock_code)
                ticker = yf.Ticker(ticker_symbol)
                hist = ticker.history(period="60d")

                if hist.empty:
                    st.error("无法获取历史数据")
                else:
                    technicals = _calc_technicals(hist)
                    st.session_state["technicals"] = technicals

                    recent = hist.tail(7).reset_index()
                    recent["Date"] = recent["Date"].dt.strftime("%Y-%m-%d")
                    prices = [{"日期": r["Date"], "开盘": round(r["Open"], 2), "收盘": round(r["Close"], 2), "最高": round(r["High"], 2), "最低": round(r["Low"], 2), "成交量": int(r["Volume"])} for _, r in recent.iterrows()]

                    news = get_news(stock_code, limit=5)
                    sections = [f"股票代码：{stock_code}", f"近 7 天行情：\n{json.dumps(prices, ensure_ascii=False, indent=2)}", f"技术指标：\n{json.dumps(technicals, ensure_ascii=False, indent=2)}"]
                    if news:
                        sections.append(f"最新新闻：\n{json.dumps(news, ensure_ascii=False, indent=2)}")
                    sections.append("\n请预测未来走势。")

                    st.session_state["prediction"] = call_llm(PREDICT_SYSTEM_PROMPT, "\n\n".join(sections))
            except Exception as exc:
                st.session_state["prediction"] = ""
                st.error(f"预测失败：{exc}")

    tech = st.session_state.get("technicals")
    if tech:
        cols = st.columns(4)
        cols[0].metric("最新价", f"{tech['最新价']}")
        cols[1].metric("RSI(14)", f"{tech['RSI(14)']}", tech["RSI状态"])
        cols[2].metric("MACD", tech["MACD状态"])
        cols[3].metric("量比", f"{tech['量比']}")

    result2 = st.session_state.get("prediction")
    if result2:
        st.markdown(result2)
    else:
        st.info("输入股票代码后点击「预测走势」，会计算技术指标并给出趋势预判。")
