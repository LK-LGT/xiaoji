#!/usr/bin/env python
"""数据库迁移脚本 - 添加 assessment_record 的新字段。"""

import sqlite3
import os

db_file = "data/perf.db"

if not os.path.exists(db_file):
    print(f"❌ 数据库不存在: {db_file}")
    exit(1)

print(f"使用数据库: {db_file}")

conn = sqlite3.connect(db_file)
cursor = conn.cursor()

try:
    # 获取现有列
    cursor.execute("PRAGMA table_info(assessment_record)")
    columns = {row[1] for row in cursor.fetchall()}
    print(f"现有列: {columns}\n")
    
    # 添加缺失的列
    migrations = [
        ("self_review_content", "TEXT"),
        ("manager_comment", "TEXT"),
    ]
    
    for col_name, col_type in migrations:
        if col_name not in columns:
            print(f"添加列: {col_name}")
            cursor.execute(f"ALTER TABLE assessment_record ADD COLUMN {col_name} {col_type}")
            print(f"  ✓ {col_name} 添加成功")
        else:
            print(f"  ✓ {col_name} 已存在，跳过")
    
    conn.commit()
    
    # 验证
    cursor.execute("PRAGMA table_info(assessment_record)")
    new_columns = [(row[1], row[2]) for row in cursor.fetchall()]
    print(f"\n✅ 数据库迁移完成！")
    print(f"现在的列: {new_columns}")
    
except Exception as e:
    print(f"❌ 迁移失败: {e}")
    conn.rollback()

finally:
    conn.close()
