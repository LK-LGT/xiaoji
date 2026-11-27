"""诊断脚本：检查飞书后台的「重定向 URL」是否配好（20029 排查）。

为什么需要这个脚本
------------------
真机授权报 **20029「重定向 URL 有误」** 时，最容易卡住的地方是
「到底配没配上」—— 人肉去开发者后台核对，在多应用场景下还容易看错应用。

飞书有**官方 API 能直接读出后台配的 redirect_urls**（`application/v6`），
所以这件事可以脚本化、可进 CI、可做巡检。

用法
----
    python backend/scripts/check_feishu_redirect_urls.py

⚠️ 需要 ``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET`` 配置。
⚠️ **只读** —— 本脚本不会修改飞书后台任何配置。

退出码
------
    0  全部正常
    1  存在未配置的 redirect_uri（会导致 20029）
    2  飞书接口调用失败（配置缺失 / 网络问题 / 应用不存在）
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

# ⚠️ **引导到仓库根 + 用 ``backend.app.*``** —— 见
# ``seed_department_workload.py`` 里同一问题的说明：
# 顶层 ``app`` 会被 agent-service 的 ``app`` 包抢占。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.config import settings  # noqa: E402

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
APP_INFO_URL = "https://open.feishu.cn/open-apis/application/v6/applications/{app_id}"


def get_app_access_token(client: httpx.Client) -> str:
    """取应用身份凭证（用于读应用信息）。"""
    resp = client.post(
        TOKEN_URL,
        json={
            "app_id": settings.feishu_app_id,
            "app_secret": settings.feishu_app_secret,
        },
    )
    data = resp.json()
    token = data.get("app_access_token") or ""
    if not token:
        raise RuntimeError(
            f"取 app_access_token 失败：code={data.get('code')} msg={data.get('msg')}"
        )
    return token


def fetch_app_info(client: httpx.Client, token: str) -> dict:
    """读应用信息（含 redirect_urls）。"""
    resp = client.get(
        APP_INFO_URL.format(app_id=settings.feishu_app_id),
        headers={"Authorization": f"Bearer {token}"},
        params={"lang": "zh_cn"},
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"查应用信息失败：code={data.get('code')} msg={data.get('msg')}"
        )
    return data["data"]["app"]


def main() -> int:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        print("✖ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return 2

    # ⚠️ ``feishu_oauth_redirect_uris`` 在 config 里是**逗号分隔的字符串**
    #    （不是 list）—— 与 ``FeishuOAuthService._allowed_redirect_uris()``
    #    的解析方式保持一致。
    raw = settings.feishu_oauth_redirect_uris or ""
    configured = [u.strip() for u in raw.split(",") if u.strip()]
    if not configured:
        print("✖ 后端未配置 FEISHU_OAUTH_REDIRECT_URIS —— 免登入口不会放行")
        return 1

    try:
        with httpx.Client(timeout=30) as client:
            token = get_app_access_token(client)
            app = fetch_app_info(client, token)
    except Exception as exc:  # noqa: BLE001
        print(f"✖ 飞书接口调用失败：{exc}")
        return 2

    backend_urls = app.get("redirect_urls") or []
    app_name = app.get("app_name") or "(未知)"

    print(f"应用        : {app_name}")
    print(f"app_id      : {settings.feishu_app_id}")
    print(f"后台已配    : {backend_urls or '[]（空！）'}")
    print()

    missing = [u for u in configured if u not in backend_urls]
    for url in configured:
        mark = "✅" if url in backend_urls else "❌ 未配置"
        print(f"  {mark}  {url}")

    if not backend_urls:
        print()
        print("⚠️ 后台 redirect_urls 为空 —— 授权必然报 20029。")
        print("   去开发者后台：应用详情 > 开发配置 > 安全设置 > 重定向 URL > 添加")
        print("   ⚠️ 确认加在**这个**应用上（多应用时极易加错），且**点保存**。")
        return 1

    if missing:
        print()
        print("⚠️ 以下地址后端在用，但飞书后台没有 —— 授权会报 20029：")
        for url in missing:
            print(f"     {url}")
        print("   请在开发者后台「安全设置 > 重定向 URL」里**逐字**添加。")
        return 1

    print()
    print("✅ 后端白名单与飞书后台完全一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
