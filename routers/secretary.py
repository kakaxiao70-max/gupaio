from __future__ import annotations

import json
import time
import traceback

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from core.config import HEADERS
from core.utils import clean_html, normalize_stock_code
from services.llm import call_llm

router = APIRouter(prefix="/api", tags=["董秘问答"])

SECRETARY_SYSTEM_PROMPT = (
    "你是一个专业的 A 股投资者教育助手。用户会给你一些上市公司董秘在互动平台上的问答内容，"
    "请用大白话帮新手解读这些回答的含义。"
    "1. 总结董秘回答的核心意思；"
    "2. 判断这个回答对公司是利好、利空还是中性；"
    "3. 给新手简单的参考建议。"
    "不要使用复杂的金融术语。"
)


def _fetch_secretary_qa(stock_code: str, limit: int = 10) -> list[dict]:
    """从东方财富互动易抓取董秘问答。"""
    url = "https://irm.cninfo.com.cn/szse/data/newLatestQuestion"
    params = {
        "pageIndex": 1,
        "pageSize": limit,
        "stockCode": stock_code,
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("data") or data.get("result") or []
    results = []
    for row in rows[:limit]:
        question = clean_html(row.get("mainContent") or row.get("question") or "")
        answer = clean_html(row.get("attachedContent") or row.get("answer") or "")
        if question and answer:
            results.append({
                "提问时间": row.get("publishDate") or row.get("createTime", ""),
                "问题": question[:300],
                "回答": answer[:500],
            })
    return results


class SecretaryRequest(BaseModel):
    stock_code: str
    question: str = ""


@router.post("/secretary")
def secretary_qa(req: SecretaryRequest):
    try:
        code = normalize_stock_code(req.stock_code)
        print(f"[secretary] 获取董秘问答 {code}")

        qa_list = _fetch_secretary_qa(code, limit=10)
        print(f"[secretary] 获取到 {len(qa_list)} 条问答")

        if not qa_list:
            return {"ok": True, "stock_code": code, "analysis": "暂无董秘问答数据。", "qa_count": 0}

        user_prompt = f"股票代码：{code}\n\n最新董秘问答：\n{json.dumps(qa_list, ensure_ascii=False, indent=2)}"
        if req.question:
            user_prompt += f"\n\n用户特别关注的问题：{req.question}"
        user_prompt += "\n\n请解读以上董秘问答内容。"

        result = call_llm(SECRETARY_SYSTEM_PROMPT, user_prompt)
        return {"ok": True, "stock_code": code, "analysis": result, "qa_count": len(qa_list)}

    except Exception as exc:
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}
