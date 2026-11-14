#!/usr/bin/env python
"""快速诊断脚本 - 检查系统是否正常运行。"""

import requests
import sys
from datetime import datetime

def print_header(text):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")

def print_status(name, status, detail=""):
    symbol = "✅" if status else "❌"
    msg = f"{symbol} {name}"
    if detail:
        msg += f" - {detail}"
    print(msg)
    return status

def main():
    print_header("🔍 绩效管理系统 - 快速诊断")
    
    all_good = True
    
    # 1. 检查后端
    print("\n📡 后端服务检查...")
    try:
        resp = requests.get("http://localhost:8000/docs", timeout=5)
        if resp.status_code == 200:
            print_status("后端 API", True, "http://localhost:8000 ✓")
        else:
            print_status("后端 API", False, f"返回状态码 {resp.status_code}")
            all_good = False
    except Exception as e:
        print_status("后端 API", False, str(e))
        all_good = False
    
    # 2. 检查前端
    print("\n🎨 前端服务检查...")
    try:
        resp = requests.get("http://localhost:5180", timeout=5)
        if resp.status_code == 200:
            print_status("前端应用", True, "http://localhost:5180 ✓")
        else:
            print_status("前端应用", False, f"返回状态码 {resp.status_code}")
            all_good = False
    except Exception as e:
        print_status("前端应用", False, str(e))
        all_good = False
    
    # 3. 检查 API 功能
    print("\n🔗 API 功能检查...")
    try:
        resp = requests.get(
            "http://localhost:8000/v1/organizations/employees?limit=1",
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                count = len(data.get("data", {}).get("items", []))
                print_status("获取员工列表", True, f"返回 {count} 条数据 ✓")
            else:
                print_status("获取员工列表", False, f"API 错误: {data.get('message')}")
                all_good = False
        else:
            print_status("获取员工列表", False, f"返回状态码 {resp.status_code}")
            all_good = False
    except Exception as e:
        print_status("获取员工列表", False, str(e))
        all_good = False
    
    # 4. 检查部门数据
    print("\n🏢 部门数据检查...")
    try:
        resp = requests.get(
            "http://localhost:8000/v1/organizations/departments",
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                count = len(data.get("data", {}).get("items", []))
                print_status("获取部门列表", True, f"返回 {count} 个部门 ✓")
            else:
                print_status("获取部门列表", False, f"API 错误: {data.get('message')}")
                all_good = False
        else:
            print_status("获取部门列表", False, f"返回状态码 {resp.status_code}")
            all_good = False
    except Exception as e:
        print_status("获取部门列表", False, str(e))
        all_good = False
    
    # 最终结果
    print_header("📊 诊断结果")
    
    if all_good:
        print("""
✅ 所有检查都通过！系统正常运行

立即访问：
  🌐 前端应用: http://localhost:5180
  📡 API 文档: http://localhost:8000/docs

测试账号：
  admin / admin123
  manager / manager123
  employee / employee123
        """)
        return 0
    else:
        print("""
❌ 发现问题，请查看上面的错误信息

常见解决方案：
  1. 检查后端是否运行: netstat -ano | findstr "8000"
  2. 检查前端是否运行: netstat -ano | findstr "5180"
  3. 尝试完全重启（参考 TROUBLESHOOTING.md）
  4. 检查防火墙是否阻止

需要更多帮助？查看 TROUBLESHOOTING.md
        """)
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n❌ 诊断出错: {e}")
        sys.exit(1)
