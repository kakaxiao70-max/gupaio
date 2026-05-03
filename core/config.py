from __future__ import annotations

import os

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

ALLOWED_ORIGINS = [
    "https://chunqiao.xo.je",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
]


def get_deepseek_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("请配置环境变量 DEEPSEEK_API_KEY")
    return key
