#!/usr/bin/env python3
"""飞书集成测试脚本。

测试飞书 API 连接、拉取组织架构。
"""

import sys
from pathlib import Path

# 添加 backend 到 path
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))

from app.core.config import settings
from app.services.feishu_client import FeishuClient

import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def main():
    """主函数。"""
    print("\n" + "=" * 60)
    print("飞书集成测试")
    print("=" * 60 + "\n")

    # 检查配置
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        print("❌ 飞书凭证未配置")
        print(f"   FEISHU_APP_ID: {settings.feishu_app_id or '未设置'}")
        print(f"   FEISHU_APP_SECRET: {'已设置' if settings.feishu_app_secret else '未设置'}")
        return False

    print("✅ 飞书凭证已配置")
    print(f"   APP_ID: {settings.feishu_app_id}")
    print(f"   TENANT_ID: {settings.feishu_tenant_id or '(未设置)'}\n")

    # 创建客户端
    try:
        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
        print("✅ 飞书客户端已创建\n")
    except Exception as e:
        print(f"❌ 创建客户端失败: {e}\n")
        return False

    # 测试获取 token
    print("📡 测试 1: 获取访问令牌...")
    try:
        token = client._get_access_token()
        print(f"✅ 成功获取 token (长度: {len(token)})\n")
    except Exception as e:
        print(f"❌ 获取 token 失败: {e}\n")
        print("   可能原因:")
        print("   1. 凭证错误（APP_ID/SECRET）")
        print("   2. 飞书应用未发布")
        print("   3. 网络连接问题\n")
        client.close()
        return False

    # 测试拉取部门
    print("📡 测试 2: 拉取部门列表...")
    try:
        departments = client.fetch_departments()
        if departments:
            print(f"✅ 成功拉取 {len(departments)} 个部门")
            for dept in departments[:3]:
                print(f"   - {dept.get('name')} (ID: {dept.get('department_id')})")
            if len(departments) > 3:
                print(f"   ... 还有 {len(departments) - 3} 个部门")
        else:
            print("⚠️  未拉取到部门（组织可能为空）")
        print()
    except Exception as e:
        print(f"❌ 拉取部门失败: {e}\n")
        print("   可能原因:")
        print("   1. 应用权限不足（未申请 contact.department:read）")
        print("   2. 飞书 API 返回错误\n")
        client.close()
        return False

    # 测试拉取员工
    print("📡 测试 3: 拉取员工列表...")
    try:
        employees = client.fetch_employees()
        if employees:
            print(f"✅ 成功拉取 {len(employees)} 个员工")
            for emp in employees[:3]:
                print(f"   - {emp.get('name')} (ID: {emp.get('user_id')})")
            if len(employees) > 3:
                print(f"   ... 还有 {len(employees) - 3} 个员工")
        else:
            print("⚠️  未拉取到员工（组织可能为空）")
        print()
    except Exception as e:
        print(f"❌ 拉取员工失败: {e}\n")
        print("   可能原因:")
        print("   1. 应用权限不足（未申请 contact.user:read）")
        print("   2. 飞书 API 返回错误\n")
        client.close()
        return False

    client.close()

    print("=" * 60)
    print("✅ 所有测试通过！飞书集成配置正确")
    print("=" * 60 + "\n")

    print("下一步:")
    print("1. 调用 POST /v1/feishu/sync-organization 手动同步组织架构")
    print("2. 检查数据库中的 Employee 和 Department 表\n")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
