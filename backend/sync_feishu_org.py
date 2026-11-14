#!/usr/bin/env python
"""同步飞书组织架构到本地数据库。"""

from app.core.db import init_db
from app.services.feishu_client import FeishuClient
from app.services.organization_sync import OrganizationSyncService
from app.core.config import settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 初始化数据库
init_db()

# 创建数据库连接
engine = create_engine(settings.database_url)
Session = sessionmaker(bind=engine)
db = Session()

# 执行同步
try:
    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )
    service = OrganizationSyncService(db, client)
    stats = service.sync_all()
    
    print("✅ 同步完成！")
    print(f"   部门数: {stats['departments_synced']}")
    print(f"   员工数: {stats['employees_synced']}")
    print(f"   错误数: {stats['errors']}")
    client.close()
except Exception as e:
    print(f"❌ 同步失败: {e}")
    import traceback
    traceback.print_exc()
finally:
    db.close()
