#!/usr/bin/env python
"""检查飞书返回的员工字段是否完整。"""

from app.core.config import settings
from app.services.feishu_client import FeishuClient

client = FeishuClient(
    app_id=settings.feishu_app_id,
    app_secret=settings.feishu_app_secret,
)

employees = client.fetch_employees(page_size=1)

if employees:
    emp = employees[0]
    print("✅ 员工数据字段检查：\n")

    required_fields = {
        'user_id': '员工 ID（必需）',
        'name': '员工名称（必需）',
        'department_ids': '部门 ID 列表（必需）',
        'leader_user_id': '直属主管 ID（必需）',
        'email': '邮箱',
        'open_id': '开放 ID',
    }

    for field, desc in required_fields.items():
        if field in emp:
            print(f"   ✅ {field}: {emp[field]} - {desc}")
        else:
            print(f"   ❌ {field}: 缺失 - {desc}")

    print("\n完整返回数据：")
    print(emp)
else:
    print("⚠️ 未拉到员工")

client.close()
