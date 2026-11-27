#!/usr/bin/env python
"""测试组织树 API 的响应内容。"""


import sys
from pathlib import Path

# ⚠️ **引导到仓库根** —— 让 `backend.app.*` 可导入。
# （顶层 `app` 会被 agent-service 的 `app` 包抢占，所以统一用全路径。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)

print("=" * 80)
print("TEST 1: GET /v1/organizations/departments/tree")
print("=" * 80)

response = client.get("/v1/organizations/departments/tree")
print(f"状态码: {response.status_code}")

data = response.json()
tree = data["data"]["tree"]

print(f"\n一级部门数: {len(tree)}")
print(f"总部门数: {data['data']['total_departments']}")
print(f"总员工数: {data['data']['total_employees']}\n")

# 美化输出树
def print_tree(node, level=0):
    indent = "  " * level
    name = node["name"]
    direct = node["direct_count"]
    total = node["total_count"]

    if direct == total:
        print(f"{indent}├─ {name} ({direct} 人)")
    else:
        print(f"{indent}├─ {name} ({direct}/{total} 人)")

    for child in node["children"]:
        print_tree(child, level + 1)

for dept in tree:
    print_tree(dept)

print("\n" + "=" * 80)
print("TEST 2: GET /v1/organizations/org-chart")
print("=" * 80)

response = client.get("/v1/organizations/org-chart")
print(f"状态码: {response.status_code}")

data = response.json()
nodes = data["data"]["nodes"]

print(f"节点数: {len(nodes)}")
print(f"统计: {data['data']['stats']}\n")

print("前 10 个节点:")
for node in nodes[:10]:
    print(f"  {node['name']:20} parent={node['parent']:20} id={node['id']}")

print("\n" + "=" * 80)
print("TEST 3: GET /v1/organizations/departments?page=1&page_size=10")
print("=" * 80)

response = client.get("/v1/organizations/departments?page=1&page_size=10")
print(f"状态码: {response.status_code}")

data = response.json()
print(f"页码: {data['data']['page']}")
print(f"每页数: {data['data']['page_size']}")
print(f"总数: {data['data']['total']}\n")

for dept in data["data"]["items"]:
    print(f"  {dept['name']:20} parent={dept['parent_id']}")

print("\n" + "=" * 80)
print("TEST 4: GET /v1/organizations/departments/{id}")
print("=" * 80)

# 查"财务部" (912ecd8a7f82e638)
dept_id = "912ecd8a7f82e638"
response = client.get(f"/v1/organizations/departments/{dept_id}")
print(f"状态码: {response.status_code}")

data = response.json()
print(f"\n部门名: {data['data']['department']['name']}")
print(f"员工数: {data['data']['total']}")
print("\n直属员工:")
for emp in data["data"]["employees"]:
    print(f"  - {emp['name']:15} {emp['position']:10} {emp['email']}")

print("\n" + "=" * 80)
print("TEST 5: GET /v1/organizations/employees?page=1&page_size=5")
print("=" * 80)

response = client.get("/v1/organizations/employees?page=1&page_size=5")
print(f"状态码: {response.status_code}")

data = response.json()
print(f"总员工: {data['data']['total']}")
print(f"本页: {len(data['data']['items'])}\n")

for emp in data["data"]["items"]:
    print(f"  - {emp['name']:15} {emp['position']:10} {emp['department_id']}")
