from __future__ import annotations

import traceback

from fastapi import APIRouter
from pydantic import BaseModel

from core.utils import normalize_stock_code
from services.llm import analyze_stock
from services.news import get_news
from services.price import get_price_data

router = APIRouter(prefix="/api", tags=["分析"])


class AnalyzeRequest(BaseModel):
    stock_code: str


@router.post("/analyze")
def analyze(req: AnalyzeRequest):
    try:
        code = normalize_stock_code(req.stock_code)
        print(f"[analyze] 开始分析 {code}")

        stock_data = get_price_data(code, days=7)
        print(f"[analyze] 价格数据：{len(stock_data['prices'])} 天，涨跌幅 {stock_data['change_pct']}%")

        news = get_news(code, limit=5)
        print(f"[analyze] 新闻：{len(news)} 条")

        result = analyze_stock(code, stock_data, news)
        return {"ok": True, "stock_code": code, "analysis": result}

    except Exception as exc:
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}
