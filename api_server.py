from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from akshare_a_stock_demo import (
    analyze_stock_with_llm,
    get_latest_news,
    get_recent_history,
    normalize_stock_code,
)


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8000))


def _json_response(handler: BaseHTTPRequestHandler, status: int, data: dict[str, Any]) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


class StockAnalysisHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        _json_response(self, 200, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            _json_response(self, 200, {"ok": True})
            return
        _json_response(self, 404, {"ok": False, "error": "接口不存在"})

    def do_POST(self) -> None:
        if self.path != "/api/analyze":
            _json_response(self, 404, {"ok": False, "error": "接口不存在"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(raw_body or "{}")
            raw_code = str(payload.get("stock_code") or "")
            provider = str(payload.get("provider") or "deepseek")

            stock_code = normalize_stock_code(raw_code)
            history_df = get_recent_history(stock_code, days=5)
            news_df = get_latest_news(stock_code, limit=5)
            analysis = analyze_stock_with_llm(
                stock_code=stock_code,
                history_df=history_df,
                news_df=news_df,
                provider=provider,
            )

            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "stock_code": stock_code,
                    "analysis": analysis,
                },
            )
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), StockAnalysisHandler)
    print(f"API server listening on http://{HOST}:{PORT}")
    print("Health check: http://localhost:8000/health")
    server.serve_forever()


if __name__ == "__main__":
    main()
