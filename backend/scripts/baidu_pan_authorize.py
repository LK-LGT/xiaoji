"""百度网盘授权 —— 命令行走一遍流程。

用完即删（或者当工具留着也行）。

为什么需要这个脚本：
    授权要在**浏览器里点**（百度要用户登录 + 勾选网盘目录），
    拿到授权码后要用服务端换 token 并落库。
    本脚本直接操作数据库，省得启后端 + 拼 API 请求。

用法：
    1. python backend/scripts/baidu_pan_authorize.py url
       → 打印授权链接，并把它存到剪贴板（如果有）
    2. 浏览器打开链接 → 登录 → 勾选网盘目录 → 同意
    3. 页面显示授权码（oob 模式），复制它
    4. python backend/scripts/baidu_pan_authorize.py code <授权码>
       → 换 token、落库、并做一次目录可读性探针
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import settings  # noqa: E402
from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.services.baidu_pan_service import BaiduPanService  # noqa: E402


def cmd_url() -> int:
    service = BaiduPanService(get_sessionmaker()())
    try:
        url = service.build_authorize_url()
        print("=" * 78)
        print("打开下面的链接，按提示操作：")
        print("=" * 78)
        print(url)
        print()
        print("⚠️ 授权页面会让你**勾选网盘目录** —— 勾了哪些，应用就能读哪些。")
        print(f"⚠️ 应用名「{settings.baidu_pan_app_name}」→ 目录会挂到 "
              f"/apps/{settings.baidu_pan_app_name}/")
        print()
        print("授权成功后页面会显示一串授权码，复制它，然后运行：")
        print("    python backend/scripts/baidu_pan_authorize.py code <授权码>")
        return 0
    finally:
        service.close()


def cmd_code(code: str) -> int:
    db = get_sessionmaker()()
    service = BaiduPanService(db)
    try:
        print("正在用授权码换 token ...")
        result = service.authorize(code)
        db.commit()
        print()
        print("✅ 授权成功")
        print(f"   scope       = {result.get('scope')}")
        print(f"   有效期       = {result.get('expires_in')} 秒（约 30 天）")
        probe = result.get("probe") or {}
        print()
        print("目录可读性探针：")
        print(f"   路径   = {probe.get('path')}")
        print(f"   可读   = {probe.get('ok')}")
        if probe.get("ok"):
            print(f"   扫到   = {probe.get('scanned')} 个文件")
            print(f"   提示   = {probe.get('hint')}")
        else:
            print(f"   错误   = {probe.get('error')}（errno={probe.get('errno')}）")
            print()
            print("⚠️ 常见原因：授权时**没勾选**网盘目录。")
            print("   重新跑 url 命令走一遍授权，勾对目录。")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"❌ 授权失败：{exc}")
        print()
        print("⚠️ 常见原因：")
        print("   · 授权码已用过（code 只能用一次）")
        print("   · 授权码超过 10 分钟")
        print("   · redirect_uri 与授权时不一致")
        return 1
    finally:
        service.close()
        db.close()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    action = sys.argv[1].lower()
    if action == "url":
        return cmd_url()
    if action == "code":
        if len(sys.argv) < 3:
            print("用法：python backend/scripts/baidu_pan_authorize.py code <授权码>")
            return 1
        return cmd_code(sys.argv[2].strip())
    print(f"未知命令：{action}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
