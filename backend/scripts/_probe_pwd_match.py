"""只读探针：确认某个口令是否与库里的哈希匹配（不修改任何数据）。"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.chdir(BACKEND)

from app.core.db import get_sessionmaker  # noqa: E402
from app.domain.models import Employee  # noqa: E402
from app.services import password_hasher  # noqa: E402

emp_id = sys.argv[1]
candidates = sys.argv[2:]

db = get_sessionmaker()()
emp = db.get(Employee, emp_id)
print(f"员工: {emp.id} {emp.name} login_account={emp.login_account!r}")
print(f"  force_change_password={emp.force_change_password} token_version={emp.token_version}")
print(f"  password_set_at={emp.password_set_at}")
print(f"  hash_prefix={(emp.password_hash or 'NULL')[:34]}")
matched = [p for p in candidates if password_hasher.verify_password(p, emp.password_hash or "")]
print(f"  匹配口令 = {matched or '（全不匹配）'}")
db.close()
