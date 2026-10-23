# -*- coding: utf-8 -*-
"""抓取模式：在校内跑一次，记录认证页真实发出的请求。

目的：搞清楚**网页扫码流程**里"手机确认之后，PC 是靠哪个调用完成上线"的——
这决定了零 Token 的扫码助手（路线 Q）能否真正落地。

它同时也会告诉你：浏览器到底有没有发 `Authorization: Bearer` 头
（这决定"从浏览器 DevTools 复制 Token"这条路是否可行）。

用法：
    pip install playwright && python -m playwright install chromium
    python capture.py            # 打开浏览器跑 60 秒，扫码一次
输出：
    capture.json  —— 完整请求记录
    capture.log   —— 人读摘要
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

OUT_JSON = Path(__file__).resolve().parent / "capture.json"
OUT_LOG = Path(__file__).resolve().parent / "capture.log"

INTEREST = ("/api/", "network", "auth", "login", "qr", "code", "user")


def _now() -> str:
    return time.strftime("%H:%M:%S")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("✗ 需要 Playwright。请先执行：")
        print("    pip install playwright")
        print("    python -m playwright install chromium")
        return 1

    from auth import CampusAuth
    from config import Config

    cfg = Config()
    auth = CampusAuth(cfg)
    ip = auth.detect_local_ip()
    mac = auth.detect_mac()
    base = cfg.base_url
    print(f"• 认证网关 : {base}")
    print(f"• 本机 IP  : {ip or '未探测到'}")
    print(f"• 本机 MAC : {mac or '未探测到'}")

    params = {
        "type": "17",
        "wlanuserip": ip,
        "wlanacname": str(cfg.get("wlan_acname") or ""),
        "clientip": ip,
        "clientmac": mac,
        "paip": str(cfg.get("paip") or ""),
        "vlan": str(cfg.get("vlan") or "0.0"),
    }
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
    target = f"{base}/web/network/code?{query}"

    records: List[Dict[str, Any]] = []
    lines: List[str] = []

    def keep(url: str) -> bool:
        u = url.lower()
        return any(k in u for k in INTEREST)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        def on_request(req):
            try:
                headers = {k.lower(): v for k, v in req.headers.items()}
                records.append(
                    {
                        "t": _now(),
                        "kind": "request",
                        "method": req.method,
                        "url": req.url,
                        "has_auth": "authorization" in headers,
                        "authorization": headers.get("authorization", ""),
                        "post_data": (req.post_data or "")[:2000],
                        "resource_type": req.resource_type,
                    }
                )
            except Exception:
                pass

        def on_response(resp):
            try:
                if not keep(resp.url):
                    return
                try:
                    body = resp.text()
                except Exception:
                    body = ""
                records.append(
                    {
                        "t": _now(),
                        "kind": "response",
                        "status": resp.status,
                        "url": resp.url,
                        "body": body[:4000],
                    }
                )
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        print()
        print("▶ 浏览器已打开。请在 60 秒内用手机扫码并确认登录。")
        print()
        page.goto(target, wait_until="domcontentloaded")

        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(1)

        # 抓一下页面里的关键变量
        try:
            js = page.evaluate(
                "() => ({code: typeof code !== 'undefined' ? code : null,"
                " vars: Object.keys(window).filter(k => /qr|code|token|auth/i.test(k)).slice(0,60)})"
            )
        except Exception:
            js = {}

        cookies = []
        try:
            cookies = ctx.cookies()
        except Exception:
            pass

        browser.close()

    OUT_JSON.write_text(
        json.dumps(
            {"target": target, "page_vars": js, "cookies": cookies, "records": records},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # -------------------------------------------------- 生成人读摘要
    lines.append("目标页面: " + target)
    lines.append("")
    lines.append("== 浏览器发出的请求（关注 /api/ 与 Authorization 头）==")
    for r in records:
        if r["kind"] != "request":
            continue
        if not keep(r["url"]):
            continue
        lines.append(
            f"[{r['t']}] {r['method']} {r['url']}"
            f"  auth={'有' if r.get('has_auth') else '无'}"
        )
        if r.get("post_data"):
            lines.append(f"      body: {r['post_data'][:300]}")
        if r.get("authorization"):
            lines.append(f"      Authorization: {r['authorization'][:120]}")

    lines.append("")
    lines.append("== 响应体 ==")
    for r in records:
        if r["kind"] != "response":
            continue
        if r["body"]:
            lines.append(f"[{r['t']}] {r['status']} {r['url']}")
            lines.append(f"      {r['body'][:600]}")

    lines.append("")
    lines.append("== 页面变量 ==")
    lines.append(json.dumps(js, ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("== Cookie ==")
    for c in cookies:
        lines.append(f"{c.get('name')} = {c.get('value')}  (domain={c.get('domain')})")

    OUT_LOG.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("✓ 抓取完成")
    print(f"  完整记录: {OUT_JSON}")
    print(f"  摘要    : {OUT_LOG}")
    print()
    print("请把 capture.log 的内容发给我，我会据此把扫码助手的「完成认证」调用写死。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover
        print(f"抓取出错: {exc}")
        sys.exit(3)
