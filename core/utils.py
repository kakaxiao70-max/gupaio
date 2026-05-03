from __future__ import annotations

import re
import time
from html import unescape
from typing import Any, Callable, TypeVar

from core.config import MAX_RETRIES, RETRY_SLEEP_SECONDS

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


def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("　", " ").replace("\r\n", " ").replace("\n", " ")
    return unescape(text).strip()
