from __future__ import annotations

import json
import traceback

import yfinance as yf
from fastapi import APIRouter
from pydantic import BaseModel

from core.utils import normalize_stock_code, to_yfinance_ticker
from services.llm import call_llm
from services.news import get_news

router = APIRouter(prefix="/api", tags=["走势预测"])

PREDICT_SYSTEM_PROMPT = (
    "你是一个资深的 A 股技术分析师。用户会给你一只股票的近期行情数据和技术指标，请你预测未来走势。"
    "请按以下格式输出（不要用星号、引号等符号）：\n"
    "1. 趋势判断：（上涨/震荡/下跌）\n"
    "2. 关键信号：（结合技术指标和新闻说明理由）\n"
    "3. 短期预判：（未来 3-5 个交易日可能的走势）\n"
    "4. 风险提示：（需要注意的风险点）\n"
    "5. 信心指数：（1-10 分，10 分最有信心）\n\n"
    "注意：面向新手解释，不要用复杂金融术语。这不是投资建议，仅供参考。"
)


def _calc_technicals(hist) -> dict:
    """计算常见技术指标。"""
    close = hist["Close"]
    volume = hist["Volume"]

    # 均线
    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]

    # RSI（14日）
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean().iloc[-1]
    rsi = 100 - (100 / (1 + gain / loss)) if loss != 0 else 50

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9).mean()
    macd_hist = macd_line - signal_line

    # 布林带
    bb_mid = close.rolling(20).mean().iloc[-1]
    bb_std = close.rolling(20).std().iloc[-1]
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # 成交量变化
    vol_avg = volume.rolling(5).mean().iloc[-1]
    vol_latest = volume.iloc[-1]
    vol_ratio = round(vol_latest / vol_avg, 2) if vol_avg > 0 else 1

    last_close = close.iloc[-1]

    return {
        "最新价": round(last_close, 2),
        "MA5": round(ma5, 2),
        "MA10": round(ma10, 2),
        "MA20": round(ma20, 2),
        "RSI(14)": round(rsi, 1),
        "MACD": round(macd_hist.iloc[-1], 4),
        "布林上轨": round(bb_upper, 2),
        "布林中轨": round(bb_mid, 2),
        "布林下轨": round(bb_lower, 2),
        "量比": vol_ratio,
        "价格vs MA5": "在上方" if last_close > ma5 else "在下方",
        "价格vs MA20": "在上方" if last_close > ma20 else "在下方",
        "RSI状态": "超买" if rsi > 70 else "超卖" if rsi < 30 else "正常",
        "MACD状态": "金叉" if macd_hist.iloc[-1] > 0 else "死叉",
    }


def _fetch_history(stock_code: str, days: int = 60):
    """获取较长周期的历史数据用于计算技术指标。"""
    ticker_symbol = to_yfinance_ticker(stock_code)
    ticker = yf.Ticker(ticker_symbol)
    hist = ticker.history(period=f"{days}d")
    if hist.empty:
        raise ValueError(f"{ticker_symbol} 无行情数据")
    return hist


class PredictRequest(BaseModel):
    stock_code: str


@router.post("/predict")
def predict(req: PredictRequest):
    try:
        code = normalize_stock_code(req.stock_code)
        print(f"[predict] 预测走势 {code}")

        # 获取 60 天历史数据（用于计算技术指标）
        hist = _fetch_history(code, days=60)
        technicals = _calc_technicals(hist)

        # 获取新闻
        news = get_news(code, limit=5)

        # 构建 prompt
        sections = [f"股票代码：{code}"]

        # 近 7 天行情
        recent = hist.tail(7).reset_index()
        recent["Date"] = recent["Date"].dt.strftime("%Y-%m-%d")
        prices = []
        for _, row in recent.iterrows():
            prices.append({
                "日期": row["Date"],
                "开盘": round(row["Open"], 2),
                "收盘": round(row["Close"], 2),
                "最高": round(row["High"], 2),
                "最低": round(row["Low"], 2),
                "成交量": int(row["Volume"]),
            })
        sections.append(f"近 7 天行情：\n{json.dumps(prices, ensure_ascii=False, indent=2)}")

        # 技术指标
        sections.append(f"技术指标：\n{json.dumps(technicals, ensure_ascii=False, indent=2)}")

        # 新闻
        if news:
            sections.append(f"最新新闻：\n{json.dumps(news, ensure_ascii=False, indent=2)}")
        else:
            sections.append("新闻：暂无")

        result = call_llm(PREDICT_SYSTEM_PROMPT, "\n\n".join(sections))
        return {"ok": True, "stock_code": code, "prediction": result, "technicals": technicals}

    except Exception as exc:
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}
