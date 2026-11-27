#!/usr/bin/env python
"""检查飞书企业和账户信息。"""

import json
import sys
from pathlib import Path

# ⚠️ **引导到仓库根** —— 让 `backend.app.*` 可导入。
# （顶层 `app` 会被 agent-service 的 `app` 包抢占，所以统一用全路径。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from backend.app.core.config import settings
from backend.app.services.feishu_client import FeishuClient

client = FeishuClient(
    app_id=settings.feishu_app_id,
    app_secret=settings.feishu_app_secret,
    tenant_id=settings.feishu_tenant_id,
)

print("🔍 飞书企业信息检查\n")

# 1. 获取当前应用的权限信息
print("📋 应用权限信息...")
try:
    data = client._request("GET", "/auth/v3/app_access_token/info")
    print("✅ 权限信息:")
    print(f"   应用 ID: {data.get('app_id')}")
    print(f"   Tenant ID: {data.get('tenant_key')}")
    print()
except Exception as e:
    print(f"⚠️ 无法获取权限信息: {e}\n")

# 2. 获取企业通讯录信息
print("🏢 尝试获取企业信息...")
try:
    data = client._request("GET", "/contact/v3/departments/1")
    print("✅ 根部门信息:")
    print(json.dumps(data.get('department', {}), indent=2, ensure_ascii=False))
    print()
except Exception as e:
    print(f"⚠️ 无法获取根部门: {e}\n")

# 3. 直接拉取第一个员工的完整信息
print("👤 第一个员工完整信息...")
try:
    employees = client.fetch_employees(page_size=1)
    if employees:
        emp = employees[0]
        print("✅ 员工详情:")
        for key in sorted(emp.keys()):
            print(f"   {key}: {emp[key]}")
    print()
except Exception as e:
    print(f"❌ 错误: {e}\n")

# 4. 直接查询这个员工的信息
print("🔎 用 user_id 直接查询员工...")
try:
    user_id = "2aaf32ea"
    emp_detail = client.fetch_employee(user_id)
    print(f"✅ {user_id} 的详细信息:")
    print(json.dumps(emp_detail, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"❌ 错误: {e}")

client.close()
