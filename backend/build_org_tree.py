#!/usr/bin/env python
"""构建公司完整的组织树。"""

from app.core.db import get_sessionmaker
from app.domain.models import Department, Employee

db = get_sessionmaker()()

# 拉所有飞书部门（id 包含特定字符，避免测试数据）
all_depts = db.query(Department).filter(
    Department.id != '0',  # 排除根部门 id=0
    ~Department.id.like('DEPT_%')  # 排除测试部门
).all()

print(f"总部门数: {len(all_depts)}\n")

# 建立 id -> dept 映射
dept_map = {d.id: d for d in all_depts}

# 统计各部门员工数（包括子部门）
def count_employees_recursive(dept_id):
    """递归统计部门及子部门的总人数。"""
    # 直属员工
    direct = db.query(Employee).filter(Employee.department_id == dept_id).count()

    # 找子部门
    children = [d for d in all_depts if d.parent_id == dept_id]

    # 递归加上子部门的人
    total = direct
    for child in children:
        total += count_employees_recursive(child.id)

    return total

# 找一级部门（parent_id=0 或不存在）
level1_depts = [d for d in all_depts if d.parent_id == '0' or d.parent_id == 0]

print("=" * 80)
print("公司组织结构树：\n")

def print_tree(dept_id, level=0):
    """递归打印组织树。"""
    dept = dept_map.get(dept_id)
    if not dept:
        return

    # 员工数统计
    direct_count = db.query(Employee).filter(Employee.department_id == dept_id).count()
    total_count = count_employees_recursive(dept_id)

    indent = "  " * level
    children = [d for d in all_depts if d.parent_id == dept_id]

    # 格式化输出
    status = "●" if total_count > 0 else "○"
    if direct_count == total_count:
        # 没有子部门的叶子部门
        print(f"{indent}{status} {dept.name} ({direct_count} 人) [{dept_id}]")
    else:
        # 有子部门的中间节点
        print(f"{indent}{status} {dept.name} ({direct_count} 人，含子部门 {total_count} 人) [{dept_id}]")

    # 递归打印子部门
    for child in sorted(children, key=lambda x: x.name or ''):
        print_tree(child.id, level + 1)

# 打印每一级部门
for dept in sorted(level1_depts, key=lambda x: x.name or ''):
    print_tree(dept.id)

# 统计汇总
print("\n" + "=" * 80)
print("统计汇总：\n")

total_emps = db.query(Employee).count()
total_depts = len(all_depts)

print(f"总员工数：{total_emps}")
print(f"一级部门数：{len(level1_depts)}")
print(f"二级及以下部门数：{total_depts - len(level1_depts)}")

# 按员工数排序，显示最大的部门
print("\n员工最多的部门（前 15）：")
dept_info = []
for dept in all_depts:
    total = count_employees_recursive(dept.id)
    if total > 0:
        dept_info.append((dept.name, total, dept.id))

for name, count, _dept_id in sorted(dept_info, key=lambda x: x[1], reverse=True)[:15]:
    print(f"  {name}: {count} 人")

db.close()
