#!/usr/bin/env python
"""检查飞书部门和员工分布。"""

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

print("🔍 飞书组织结构检查\n")

# 1. 拉取部门
print("📂 拉取所有部门...")
try:
    departments = client.fetch_departments()
    print(f"✅ 拉取到 {len(departments)} 个部门\n")

    for dept in departments[:10]:
        print(f"  - {dept.get('name')} (ID: {dept.get('department_id')})")
        print(f"    主管: {dept.get('leader_user_id')}")
        print(f"    父部门: {dept.get('parent_department_id')}")
        print()
except Exception as e:
    print(f"❌ 拉取部门失败: {e}\n")

# 2. 按部门拉取员工
print("👥 按部门拉取员工...")
if departments:
    for dept in departments[:3]:
        dept_id = dept.get('department_id')
        dept_name = dept.get('name')
        print(f"\n部门: {dept_name} ({dept_id})")

        try:
            # 飞书 API: department_id_type 指定用什么 ID 查询
            data = client._request("GET", "/contact/v3/users",
                params={"department_id": dept_id, "department_id_type": "department_id", "page_size": 50})

            items = data.get("items", [])
            print(f"  员工数: {len(items)}")
            for emp in items[:3]:
                print(f"    - {emp.get('name')} ({emp.get('user_id')})")
        except Exception as e:
            print(f"  ❌ 错误: {e}")

# 3. 尝试用 user_id_type 变体
print("\n\n🔄 尝试 user_id_type 参数变体...")
try:
    for user_id_type in ["user_id", "open_id", "union_id"]:
        data = client._request("GET", "/contact/v3/users",
            params={"page_size": 50, "user_id_type": user_id_type})
        items = data.get("items", [])
        print(f"  user_id_type={user_id_type}: {len(items)} 人")
except Exception as e:
    print(f"  错误: {e}")

client.close()
print("\n✅ 检查完成")
