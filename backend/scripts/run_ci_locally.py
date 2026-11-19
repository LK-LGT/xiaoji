#!/usr/bin/env python
"""本地复现 CI 全流程的校验脚本。

用途：在推送到远端之前，用**完全相同的命令**跑一遍 CI 的所有检查，
避免「本地通过、CI 失败」。

用法：
    python backend/scripts/run_ci_locally.py              # 跑全部检查
    python backend/scripts/run_ci_locally.py --backend    # 只跑后端
    python backend/scripts/run_ci_locally.py --frontend   # 只跑前端
    python backend/scripts/run_ci_locally.py --agent      # 只跑智能体服务

退出码：
    0 = 全部通过
    1 = 有检查失败
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

IS_WINDOWS = os.name == "nt"
PYTHON = sys.executable
NPM = "npm.cmd" if IS_WINDOWS else "npm"


class Check:
    """一次检查的执行结果。"""

    def __init__(self, name: str, cmd: list[str], cwd: Path, env: dict[str, str] | None = None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.ok = False
        self.seconds = 0.0
        self.output = ""

    def run(self) -> bool:
        env = {**os.environ, **(self.env or {})}
        print(f"▶ {self.name}")
        print(f"  $ {' '.join(self.cmd)}")
        started = time.time()
        try:
            proc = subprocess.run(
                self.cmd,
                cwd=self.cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            self.output = (proc.stdout or "") + (proc.stderr or "")
            self.ok = proc.returncode == 0
        except Exception as exc:  # pragma: no cover
            self.output = str(exc)
            self.ok = False
        self.seconds = time.time() - started

        status = "✔" if self.ok else "✖"
        print(f"  {status} {self.name}  ({self.seconds:.1f}s)")
        if not self.ok:
            tail = "\n".join(self.output.strip().splitlines()[-25:])
            print("\n".join(f"    {line}" for line in tail.splitlines()))
        print()
        return self.ok


def backend_checks() -> list[Check]:
    # ⚠️ 必须用**绝对路径**：
    #    - 应用（仓库根运行）解析 `sqlite:///./data/x.db` → `<root>/data/x.db`
    #    - Alembic（backend/ 目录运行）解析同一相对路径 → `<root>/backend/data/x.db`
    #    两者会落到不同的文件上，导致 schema 校验查了个空库。
    #    用绝对路径可保证无论从哪个 CWD 运行都指向同一个文件。
    db_path = (REPO_ROOT / "data" / "_ci_local.db").resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ci_db = f"sqlite:///{db_path.as_posix()}"
    env = {
        "DATABASE_URL": ci_db,
        "ALEMBIC_EXPECT_TARGET": f"sqlite:{db_path.as_posix()}",
        "AUTO_CREATE_TABLES": "false",
        "MODEL_MODE": "echo",
        "FEISHU_SYNC_ENABLED": "false",
    }
    return [
        Check(
            "API 契约校验（严格模式）",
            [PYTHON, "backend/scripts/check_api_contract.py", "--strict"],
            REPO_ROOT,
            env,
        ),
        Check(
            "后端测试",
            [
                PYTHON,
                "-m",
                "pytest",
                "backend/app/tests",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
            ],
            REPO_ROOT,
            env,
        ),
        Check(
            "Alembic 迁移 —— 升级到 head",
            [PYTHON, "-m", "alembic", "upgrade", "head"],
            REPO_ROOT / "backend",
            env,
        ),
        Check(
            "Alembic 迁移 —— 降级再升级（往返无损）",
            [PYTHON, "-m", "alembic", "downgrade", "-1"],
            REPO_ROOT / "backend",
            env,
        ),
        Check(
            "Alembic 迁移 —— 再次升级",
            [PYTHON, "-m", "alembic", "upgrade", "head"],
            REPO_ROOT / "backend",
            env,
        ),
        Check(
            "数据库 schema 校验（阶段三 9 张新表）",
            [PYTHON, "backend/scripts/verify_phase3_schema.py", str(db_path)],
            REPO_ROOT,
            env,
        ),
        Check(
            "Python 静态检查（ruff）",
            # ⚠️ R22：不再传 `--select E9,F63,F7,F82`（只查致命错误）。
            #    规则集统一由 pyproject.toml 的 [tool.ruff.lint] 提供：
            #    E + F + W + I + UP + B，豁免清单也在那里。
            #    必须与 CI（.github/workflows/ci.yml）保持同一份配置，
            #    否则「本地绿 / CI 红」或反之。
            #
            # ⚠️ 覆盖范围与 CI 完全一致：仓库根目录（`.`）——
            #    涵盖 backend/、agent-service/ 以及根目录的手动脚本。
            #    原来只查 `backend/app backend/scripts`，
            #    `backend/` 根目录脚本和整个 agent-service 都漏在门禁外。
            #
            # ⚠️ 命令写成**一行**是有意的 ——
            #    `test_ruff_ci_consistency.py` 用正则从两个文件里抽取
            #    ruff 调用做比对（覆盖路径必须一致）。
            #    拆成多行列表元素会让正则匹配不到，护栏就失效了。
            #    ⚠️ 同理，本注释里**不能**出现字面的 ruff 调用文本，
            #    否则会被当成真的调用参与比对。
            [PYTHON, "-m", "ruff", "check", "."],
            REPO_ROOT,
        ),
    ]


def frontend_checks() -> list[Check]:
    return [
        Check(
            "TypeScript 类型检查（vue-tsc）",
            [NPM, "exec", "--", "vue-tsc", "--noEmit"],
            REPO_ROOT / "frontend",
        ),
        Check(
            # ⚠️ 2026-11-16：从「基线感知」改为**零失败硬门槛**。
            #    10 个历史遗留失败已全部修完，基线机制（tests-baseline.json
            #    + scripts/run-tests-with-baseline.mjs）已删除。
            "前端单元测试（零失败硬门槛）",
            ["node", "--experimental-strip-types", "--test", "tests/*.test.ts"],
            REPO_ROOT / "frontend",
        ),
    ]


def agent_checks() -> list[Check]:
    return [
        Check(
            "智能体服务测试",
            [
                PYTHON,
                "-m",
                "pytest",
                "tests",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
            ],
            REPO_ROOT / "agent-service",
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="本地复现 CI 检查")
    parser.add_argument("--backend", action="store_true", help="只跑后端检查")
    parser.add_argument("--frontend", action="store_true", help="只跑前端检查")
    parser.add_argument("--agent", action="store_true", help="只跑智能体服务检查")
    args = parser.parse_args()

    only = args.backend or args.frontend or args.agent
    groups: list[tuple[str, list[Check]]] = []
    if not only or args.backend:
        groups.append(("后端", backend_checks()))
    if not only or args.frontend:
        groups.append(("前端", frontend_checks()))
    if not only or args.agent:
        groups.append(("智能体服务", agent_checks()))

    print("=" * 72)
    print("本地 CI 复现")
    print("=" * 72)
    print()

    results: list[Check] = []
    for title, checks in groups:
        print(f"── {title} ──")
        for check in checks:
            check.run()
            results.append(check)

    passed = sum(1 for c in results if c.ok)
    failed = len(results) - passed
    total_seconds = sum(c.seconds for c in results)

    print("=" * 72)
    print(f"结果: {passed}/{len(results)} 项通过，{failed} 项失败  ({total_seconds:.1f}s)")
    if failed:
        print()
        for c in results:
            if not c.ok:
                print(f"  ✖ {c.name}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
