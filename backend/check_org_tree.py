#!/usr/bin/env python
"""检查部门数据结构，建立组织树。"""

from app.core.db import get_sessionmaker
from app.domain.models import Employee, Department
import json

db = get_sessionmaker()()

print("飞书部门结构（前 10 个）:\n")

depts = db.query(Department).filter(
    Department.id.like('%ecd8%')  # 飞书真实部门的特征
).limit(10).all()

for d in depts:
    print(f"部门: {d.name or '(空)'}")
    print(f"  ID: {d.id}")
    print(f"  parent_id: {d.parent_id}")
    print(f"  leader_id: {d.leader_id}")
    print()

# 检查是否有树结构
print("\n" + "="*60)
print("组织树结构分析:\n")

all_depts = db.query(Department).all()
dept_map = {d.id: d for d in all_depts}

# 找根部门
roots = [d for d in all_depts if not d.parent_id or d.parent_id == d.id or d.parent_id == "0"]
print(f"根部门数: {len(roots)}")
for r in roots:
    print(f"  - {r.name or '(空)'} (ID: {r.id})")

# 检查有 parent_id 的部门数
with_parent = [d for d in all_depts if d.parent_id and d.parent_id != d.id and d.parent_id != "0"]
print(f"\n有父部门的部门数: {len(with_parent)}")

# 尝试从飞书数据恢复树（按字段打印）
print("\n飞书部门完整字段：")
for d in db.query(Department).filter(Department.id.like('%ecd8%')).limit(3).all():
    print(f"  {d.name}:")
    for k, v in d.__dict__.items():
        if not k.startswith('_'):
            print(f"    {k}: {v}")

db.close()
