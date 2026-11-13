#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.core.config import settings
from app.services.feishu_client import FeishuClient
import json

client = FeishuClient(
    app_id=settings.feishu_app_id,
    app_secret=settings.feishu_app_secret,
    tenant_id=settings.feishu_tenant_id,
)

print('直接从飞书 API 拉取数据:')
print('=' * 80)

print('\n📋 员工列表:')
employees = client.fetch_employees()
print(f'总数: {len(employees)} 个员工\n')
for emp in employees:
    print(f'  - ID: {emp.get("user_id")}')
    print(f'    姓名: {emp.get("name")}')
    print(f'    邮箱: {emp.get("email")}')
    print(f'    部门: {emp.get("department_ids")}')
    print(f'    职位: {emp.get("job_title")}')
    print(f'    主管: {emp.get("leader_user_id")}')
    print()

print('📂 部门列表:')
departments = client.fetch_departments()
print(f'总数: {len(departments)} 个部门\n')
for dept in departments:
    print(f'  - ID: {dept.get("department_id")}')
    print(f'    名称: {dept.get("name")}')
    print(f'    父部门: {dept.get("parent_department_id")}')
    print(f'    主管: {dept.get("leader_user_id")}')
    print()

print('原始 JSON 数据:')
print(json.dumps(employees, indent=2, ensure_ascii=False))

client.close()
