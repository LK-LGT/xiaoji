#!/usr/bin/env python
"""检查数据库结构。"""

import os
import sqlite3

dbs = [
    'performance_management.db',
    'data/perf.db',
    '../data/perf.db',
]

for db in dbs:
    if os.path.exists(db):
        try:
            conn = sqlite3.connect(db)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cursor.fetchall()]
            print(f"\n✓ {db}")
            print(f"  表数: {len(tables)}")
            print(f"  表列表: {tables[:5]}")

            if 'assessment_record' in tables:
                cursor.execute("PRAGMA table_info(assessment_record)")
                columns = [(row[1], row[2]) for row in cursor.fetchall()]
                print(f"  assessment_record 列: {columns}")

            conn.close()
        except Exception as e:
            print(f"✗ {db}: {e}")
    else:
        print(f"✗ {db}: 不存在")
