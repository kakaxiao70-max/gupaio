from __future__ import annotations

import json
import time

import requests

from core.config import HEADERS
from core.utils import clean_html


def _fetch_eastmoney(stock_code: str, limit: int) -> list[dict]:
    """从东方财富搜索接口获取个股新闻。"""
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner = {
        "uid": "",
        "keyword": stock_code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": limit,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    params = {"cb": "cb", "param": json.dumps(inner, ensure_ascii=False), "_": str(int(time.time() * 1000))}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("cb(") and text.endswith(")"):
        text = text[3:-1]
    rows = json.loads(text).get("result", {}).get("cmsArticleWebOld") or []
    return [
        {"时间": r.get("date", ""), "来源": r.get("mediaName", ""), "标题": clean_html(r.get("title", "")), "内容": clean_html(r.get("content", ""))[:200]}
        for r in rows[:limit]
    ]


def _fetch_sina(stock_code: str, limit: int) -> list[dict]:
    """从新浪财经滚动新闻 API 抓取。"""
    url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num={limit}&page=1&r=0.1&callback=cb"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("cb(") and text.endswith(")"):
        text = text[3:-1]
    items = json.loads(text).get("result", {}).get("data") or []
    results = []
    for item in items[:limit * 2]:
        title = clean_html(item.get("title", ""))
        intro = clean_html(item.get("intro", ""))
        if stock_code in title or stock_code in intro:
            results.append({"时间": item.get("ctime", ""), "来源": item.get("media_name", "新浪财经"), "标题": title, "内容": intro[:200]})
    return results[:limit]


def get_news(stock_code: str, limit: int = 5) -> list[dict]:
    """多源聚合获取新闻，任一源失败不影响其他源。"""
    all_news: list[dict] = []
    seen: set[str] = set()

    for name, fetcher in [("东方财富", _fetch_eastmoney), ("新浪财经", _fetch_sina)]:
        try:
            items = fetcher(stock_code, limit)
            for item in items:
                if item["标题"] and item["标题"] not in seen:
                    seen.add(item["标题"])
                    all_news.append(item)
            print(f"[news] {name}：获取 {len(items)} 条")
        except Exception as exc:
            print(f"[news] {name}失败：{exc}")

    if not all_news:
        print("[news] 所有新闻源均失败，将仅用行情数据进行分析")

    return all_news[:limit]
