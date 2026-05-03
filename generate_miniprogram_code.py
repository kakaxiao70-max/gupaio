from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8")

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WXA_CODE_URL = "https://api.weixin.qq.com/wxa/getwxacodeunlimit"


def get_access_token(appid: str, appsecret: str) -> str:
    response = requests.get(
        TOKEN_URL,
        params={
            "grant_type": "client_credential",
            "appid": appid,
            "secret": appsecret,
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败：{data}")
    return data["access_token"]


def generate_wxacode(
    access_token: str,
    scene: str,
    page: str,
    width: int,
    env_version: str,
) -> bytes:
    if len(scene) > 32:
        raise ValueError("scene 最多 32 个字符，建议只放股票代码等短参数")

    payload: dict[str, Any] = {
        "scene": scene,
        "page": page,
        "width": width,
        "env_version": env_version,
        "check_path": False,
    }
    response = requests.post(
        WXA_CODE_URL,
        params={"access_token": access_token},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        raise RuntimeError(f"生成小程序码失败：{response.text}")

    content = response.content
    if content.startswith(b"{"):
        try:
            error = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError:
            error = content.decode("utf-8", errors="replace")
        raise RuntimeError(f"生成小程序码失败：{error}")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="生成微信小程序码")
    parser.add_argument("--scene", default="code=688256", help="扫码后传给小程序的 scene 参数")
    parser.add_argument("--page", default="pages/index/index", help="小程序页面路径")
    parser.add_argument("--width", type=int, default=430, help="小程序码宽度，建议 280-1280")
    parser.add_argument(
        "--env-version",
        default="trial",
        choices=["release", "trial", "develop"],
        help="release 正式版，trial 体验版，develop 开发版",
    )
    parser.add_argument("--output", default="miniprogram_code.png", help="输出 PNG 文件")
    args = parser.parse_args()

    appid = os.getenv("WECHAT_APPID")
    appsecret = os.getenv("WECHAT_APPSECRET")
    if not appid or not appsecret:
        raise RuntimeError("请先在 .env 中配置 WECHAT_APPID 和 WECHAT_APPSECRET")

    access_token = get_access_token(appid, appsecret)
    image = generate_wxacode(
        access_token=access_token,
        scene=args.scene,
        page=args.page,
        width=args.width,
        env_version=args.env_version,
    )
    output_path = PROJECT_ROOT / args.output
    output_path.write_bytes(image)
    print(f"小程序码已生成：{output_path}")


if __name__ == "__main__":
    main()
