from __future__ import annotations

from typing import Any

import yfinance as yf

from core.utils import to_yfinance_ticker


def get_price_data(stock_code: str, days: int = 7) -> dict[str, Any]:
    """通过 yfinance 获取行情 + 基本面，失败时返回空结构。"""
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
