from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from akshare_a_stock_demo import (
    analyze_stock_with_llm,
    get_latest_news,
    get_recent_history,
    normalize_stock_code,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        history_df = get_recent_history(stock_code, days=5)
        news_df = get_latest_news(stock_code, limit=5)
        analysis = analyze_stock_with_llm(
            stock_code=stock_code,
            history_df=history_df,
            news_df=news_df,
            provider=req.provider,
        )
        return {"ok": True, "stock_code": stock_code, "analysis": analysis}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
