#!/usr/bin/env python
"""验证数据库中的飞书同步数据。"""

import sys
from pathlib import Path

# ⚠️ **引导到仓库根** —— 让 `backend.app.*` 可导入。
# （顶层 `app` 会被 agent-service 的 `app` 包抢占，所以统一用全路径。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from backend.app.core.db import get_sessionmaker
from backend.app.domain.models import Department, Employee

db = get_sessionmaker()()

dept_count = db.query(Department).count()
emp_count = db.query(Employee).count()
print(f"部门数: {dept_count}")
print(f"员工数: {emp_count}\n")

print("部门列表（含人数）:")
for d in db.query(Department).all():
    n = db.query(Employee).filter(Employee.department_id == d.id).count()
    print(f"  {d.name or '(根部门)'} ({d.id}) - {n} 人")

print("\n前 10 个员工:")
for e in db.query(Employee).limit(10).all():
    print(f"  {e.name} ({e.id}) - 部门:{e.department_id} - 职位:{e.position or '-'}")

db.close()
