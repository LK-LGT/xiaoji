"""核查：webhook 能否处理飞书**真实**事件格式（加密 + 明文两种模式）。

飞书后台已配置 Encrypt Key（加密策略），因此实际推送形态是::

    { "encrypt": "<base64(iv + AES-256-CBC(SHA256(key), PKCS7(明文)))>" }

本脚本用**真实凭据**构造加密事件，验证：
1. 加密模式 → 验签 + 解密 → 正常处理
2. 错误签名 → 必须被拒
3. 明文模式 → 比对 Verification Token → 正常处理
4. 握手 → 原样返回 challenge

⚠️ **凭据不写死在脚本里** —— 从环境变量读取，避免密钥进版本库。

用法：
    set FEISHU_ENCRYPT_KEY=<Encrypt Key>
    set FEISHU_APPROVAL_WEBHOOK_TOKEN=<Verification Token>
    python backend/scripts/check_feishu_event_format.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.core.config import settings  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.services import feishu_event  # noqa: E402

# 凭据从环境变量读（不落盘、不进版本库）
ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "")
VERIFICATION_TOKEN = os.environ.get("FEISHU_APPROVAL_WEBHOOK_TOKEN", "")

WEBHOOK_URL = "/v1/feishu/approval/events"


def encrypt_payload(plain_obj: dict, encrypt_key: str) -> str:
    """按飞书官方算法加密事件体。"""
    from Crypto.Cipher import AES

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = b"\x00" * 16
    data = json.dumps(plain_obj, ensure_ascii=False).encode("utf-8")
    pad_len = 16 - len(data) % 16
    data += bytes([pad_len]) * pad_len
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(iv + cipher.encrypt(data)).decode("utf-8")


def sign_request(encrypt_key: str, raw_body: bytes, timestamp: str, nonce: str) -> str:
    """按飞书官方算法算签名。"""
    b1 = f"{timestamp}{nonce}{encrypt_key}".encode()
    return hashlib.sha256(b1 + raw_body).hexdigest()


def real_v2_event(token: str = VERIFICATION_TOKEN) -> dict:
    """飞书真实 v2.0 审批事件（结构取自官方文档逐字模板）。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": "b53e80a132ec090ba249a8bb74da0123",
            "token": token,
            "event_type": "approval.approval.updated_v4",
            "app_id": "cli_xxx",
            "tenant_key": "d6ae59e175fxxx",
            "event_create_time": "1502199207.7171419",
        },
        "event": {
            "object": {
                "approval_id": "74333067777841234",
                "approval_code": "5B43240B-848C-49BD-9D27-11EF0EE11234",
                "version_id": "7459343285667611234",
                "widget_group_type": 0,
                "form_definition_id": "7459341670005371234",
                "timestamp": "1736763698",
                "status": "APPROVED",
                "extra": "",
            }
        },
    }


def main() -> int:
    failures: list[str] = []

    if not ENCRYPT_KEY or not VERIFICATION_TOKEN:
        print("❌ 缺少凭据 —— 请先设置环境变量：")
        print("     set FEISHU_ENCRYPT_KEY=<Encrypt Key>")
        print("     set FEISHU_APPROVAL_WEBHOOK_TOKEN=<Verification Token>")
        return 1

    old_token = settings.feishu_approval_webhook_token
    old_key = settings.feishu_encrypt_key
    settings.feishu_approval_webhook_token = VERIFICATION_TOKEN

    try:
        with TestClient(app) as c:
            # ── 1. 加密模式（真实配置）──────────────────────────
            print("=== 1. 加密模式（后台已配 Encrypt Key）===")
            settings.feishu_encrypt_key = ENCRYPT_KEY
            raw = json.dumps(
                {"encrypt": encrypt_payload(real_v2_event(), ENCRYPT_KEY)}
            ).encode("utf-8")
            ts, nonce = "1700000000", "n1"
            sig = sign_request(ENCRYPT_KEY, raw, ts, nonce)
            r = c.post(
                WEBHOOK_URL,
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    feishu_event.HEADER_TIMESTAMP: ts,
                    feishu_event.HEADER_NONCE: nonce,
                    feishu_event.HEADER_SIGNATURE: sig,
                },
            )
            ok = r.status_code == 200
            print(f"  HTTP {r.status_code}  {'OK' if ok else 'FAIL'}   {r.text[:120]}")
            if not ok:
                failures.append(f"加密模式返回 {r.status_code}")

            # ── 2. 签名错误必须被拒 ───────────────────────────
            print()
            print("=== 2. 签名错误必须拒绝（防伪造）===")
            r = c.post(
                WEBHOOK_URL,
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    feishu_event.HEADER_TIMESTAMP: ts,
                    feishu_event.HEADER_NONCE: nonce,
                    feishu_event.HEADER_SIGNATURE: "invalid",
                },
            )
            ok = r.status_code == 401
            print(f"  HTTP {r.status_code}  {'OK' if ok else 'FAIL'}   {r.text[:120]}")
            if not ok:
                failures.append(f"错误签名返回 {r.status_code}，期望 401")

            # ── 3. 明文模式 ───────────────────────────────────
            print()
            print("=== 3. 明文模式（兼容未配 Encrypt Key 的场景）===")
            settings.feishu_encrypt_key = ""
            r = c.post(WEBHOOK_URL, json=real_v2_event())
            ok = r.status_code == 200
            print(f"  HTTP {r.status_code}  {'OK' if ok else 'FAIL'}   {r.text[:120]}")
            if not ok:
                failures.append(f"明文模式返回 {r.status_code}")

            # ── 4. 握手 ───────────────────────────────────────
            print()
            print("=== 4. 回调地址握手（url_verification）===")
            r = c.post(
                WEBHOOK_URL,
                json={
                    "challenge": "ajls384kdj1234",
                    "type": "url_verification",
                    "token": VERIFICATION_TOKEN,
                },
            )
            ok = r.status_code == 200 and r.json().get("challenge") == "ajls384kdj1234"
            print(f"  HTTP {r.status_code}  {'OK' if ok else 'FAIL'}   {r.text[:120]}")
            if not ok:
                failures.append(f"握手返回 {r.status_code} / {r.text[:80]}")
    finally:
        settings.feishu_approval_webhook_token = old_token
        settings.feishu_encrypt_key = old_key

    print()
    if failures:
        print("❌ 校验失败：")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("✅ 飞书事件格式校验全部通过 —— webhook 可接收真实事件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
