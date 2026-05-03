from __future__ import annotations

import streamlit as st

from akshare_a_stock_demo import (
    analyze_stock_with_llm,
    get_latest_news,
    get_recent_history,
    normalize_stock_code,
)


st.set_page_config(page_title="A股新手分析助手", layout="centered")

st.title("A股新手分析助手")
st.caption("输入股票代码，自动获取最近行情和新闻，并用大模型生成一份大白话分析。")


def run_analysis(raw_code: str) -> str:
    stock_code = normalize_stock_code(raw_code)
    history_df = get_recent_history(stock_code, days=5)
    news_df = get_latest_news(stock_code, limit=5)
    return analyze_stock_with_llm(
        stock_code=stock_code,
        history_df=history_df,
        news_df=news_df,
        provider="deepseek",
    )


with st.form("stock_analysis_form"):
    stock_code_input = st.text_input(
        "股票代码",
        value="688256",
        placeholder="例如：688256",
        max_chars=12,
    )
    submitted = st.form_submit_button("开始分析", type="primary", width="stretch")

if submitted:
    with st.spinner("正在获取行情、新闻并调用大模型分析..."):
        try:
            st.session_state["analysis_result"] = run_analysis(stock_code_input)
        except Exception as exc:
            st.session_state["analysis_result"] = ""
            st.error(f"分析失败：{exc}")

st.divider()
st.subheader("分析结果")

analysis_result = st.session_state.get("analysis_result")
if analysis_result:
    st.markdown(analysis_result)
else:
    st.info("点击“开始分析”后，这里会显示大模型生成的 Markdown 分析结果。")
