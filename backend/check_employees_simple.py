#!/usr/bin/env python
"""简化版飞书员工拉取检查（只显示结果）。"""

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

print("🔍 飞书员工拉取检查\n")

page_token = None
page_num = 0
total_employees = []

while True:
    page_num += 1

    params = {
        "page_size": 50,
        "department_id_type": "department_id",
    }
    if page_token:
        params["page_token"] = page_token

    try:
        data = client._request("GET", "/contact/v3/users", params=params)

        items = data.get("items", [])
        total_employees.extend(items)

        print(f"第 {page_num} 页：{len(items)} 人，累计：{len(total_employees)} 人")

        page_token = data.get("page_token")
        if not page_token:
            break

    except Exception as e:
        print(f"错误: {e}")
        break

print(f"\n✅ 总计拉取 {len(total_employees)} 个员工\n")

# 显示前 10 个
print("前 10 个员工：")
for i, emp in enumerate(total_employees[:10], 1):
    name = emp.get('name', '未知')
    emp_id = emp.get('user_id', 'N/A')
    dept = emp.get('department_ids', ['N/A'])[0] if emp.get('department_ids') else 'N/A'
    print(f"  {i}. {name} ({emp_id}) - 部门: {dept}")

# 按部门统计
if total_employees:
    depts = {}
    for emp in total_employees:
        dept_ids = emp.get("department_ids", [])
        if dept_ids:
            dept_id = str(dept_ids[0])
            if dept_id not in depts:
                depts[dept_id] = 0
            depts[dept_id] += 1

    print(f"\n按部门统计 ({len(depts)} 个部门)：")
    for dept_id, count in sorted(depts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {dept_id}: {count} 人")

client.close()
