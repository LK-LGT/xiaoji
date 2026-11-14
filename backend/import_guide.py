#!/usr/bin/env python
"""CSV 批量导入使用示例。"""

# ============================================================================
# 使用说明
# ============================================================================

"""
飞书权限问题导致无法直接拉取所有 92 个员工？
没关系！现在支持 CSV 批量导入功能。

=== 快速开始 ===

## 1. 在飞书导出员工数据

1. 进入飞书企业 → 通讯录
2. 选择需要导出的部门或全部员工
3. 点击导出 → 选择 Excel 格式
4. 得到一个包含员工列表的 Excel 文件

## 2. 转换为 CSV 格式

打开 Excel 文件，确保有以下列（表头）：
- id: 员工 ID
- name: 员工名称
- email: 邮箱
- department_id: 部门 ID
- position: 职位
- manager_id: 直属主管 ID (可选)
- employment_status: 在职状态，取值 active/inactive/exited (可选，默认 active)

然后另存为 CSV（逗号分隔）。

## 3. 上传到系统

使用 curl 命令上传：

```bash
curl -X POST "http://localhost:8000/v1/import/employees-csv" \
  -F "file=@employees.csv"
```

或使用 Python requests:

```python
import requests

with open('employees.csv', 'rb') as f:
    files = {'file': f}
    response = requests.post(
        'http://localhost:8000/v1/import/employees-csv',
        files=files
    )
    print(response.json())
```

## 4. 查看导入结果

响应示例：
{
  "code": 0,
  "message": "员工导入完成",
  "data": {
    "total": 92,
    "imported": 85,
    "updated": 7,
    "errors": 0,
    "error_details": []
  }
}

=== CSV 文件示例 ===

employees.csv:
```
id,name,email,department_id,position,manager_id,employment_status
emp001,张三,zhang@company.com,dept_001,经理,emp000,active
emp002,李四,li@company.com,dept_001,员工,emp001,active
emp003,王五,wang@company.com,dept_002,总监,emp000,active
```

=== 部门导入 ===

同样支持部门批量导入，CSV 格式：

departments.csv:
```
id,name,parent_id,leader_id,active
dept_001,销售部,,emp003,true
dept_002,技术部,,emp004,true
dept_003,销售一团,dept_001,emp001,true
```

上传命令：
```bash
curl -X POST "http://localhost:8000/v1/import/departments-csv" \
  -F "file=@departments.csv"
```

=== 常见问题 ===

Q: 如果员工 ID 已经存在？
A: 会执行更新操作（updated 字段会增加）

Q: 如果导入出错？
A: 错误会记录在 error_details 中，其他行会继续导入

Q: 如何确认导入成功？
A: 可以调用 API 查询员工列表：
   curl http://localhost:8000/api/employees

"""

# ============================================================================
# 快速测试脚本
# ============================================================================

import csv
import io
import requests
from pathlib import Path

def create_sample_csv():
    """创建示例 CSV 文件。"""
    
    # 员工示例数据
    employees_data = [
        ["id", "name", "email", "department_id", "position", "manager_id", "employment_status"],
        ["emp_001", "张三", "zhang@company.com", "dept_001", "销售经理", "emp_000", "active"],
        ["emp_002", "李四", "li@company.com", "dept_001", "销售代表", "emp_001", "active"],
        ["emp_003", "王五", "wang@company.com", "dept_002", "技术总监", "emp_000", "active"],
        ["emp_004", "赵六", "zhao@company.com", "dept_002", "技术人员", "emp_003", "active"],
    ]
    
    # 写入 CSV
    csv_path = Path("sample_employees.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(employees_data)
    
    print(f"✅ 创建示例文件: {csv_path}")
    return csv_path

def upload_csv(csv_path, api_url="http://localhost:8000"):
    """上传 CSV 文件。"""
    
    with open(csv_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f"{api_url}/v1/import/employees-csv",
            files=files
        )
    
    result = response.json()
    print(f"\n📊 导入结果:")
    print(f"   状态码: {result.get('code')}")
    print(f"   消息: {result.get('message')}")
    
    if "data" in result:
        data = result["data"]
        print(f"   总计: {data.get('total')}")
        print(f"   新增: {data.get('imported')}")
        print(f"   更新: {data.get('updated')}")
        print(f"   错误: {data.get('errors')}")
        if data.get('error_details'):
            print(f"   错误详情:")
            for err in data['error_details'][:5]:  # 显示前 5 个错误
                print(f"     - {err}")

if __name__ == "__main__":
    # 创建示例 CSV
    csv_path = create_sample_csv()
    
    # 可选：上传到系统（需要先启动后端）
    # upload_csv(csv_path)
