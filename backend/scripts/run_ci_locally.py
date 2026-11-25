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
        # ⚠️⚠️ **必须提供测试用认证密钥**（2026-11-25）
        #
        # ⚠️ **这是「CI 从来没绿过」的根因**
        #   ``Settings._validate_auth_secret`` 要求
        #   ``AUTH_TOKEN_SECRET`` 至少 32 位且不得是默认值
        #   （默认 ``"change-me-in-production"`` 在弱密钥名单里）
        #   ⇒ **CI 没有 .env** ⇒ 用默认值 ⇒ **validator 直接抛 ValidationError**
        #   ⇒ ``from app.main import app`` 失败
        #   ⇒ **凡是 import app 的步骤全挂**：契约校验、生产姿态核对、
        #      后端测试、迁移、schema 校验、E2E 全挂。
        #
        # ⚠️ **为什么本地不复现**
        #   本地有 ``backend/.env``（含合法密钥）⇒ 配置能过校验
        #   ⇒ 「本地全绿 / CI 全红」，且连续 22 次 CI 没定位到。
        #
        # ⚠️ **为什么本地 CI 也要设**
        #   不设的话，本地 CI 依赖开发者本机 ``.env`` ⇒
        #   「有 .env 的机器绿 / 没有的机器红」⇒ 又是同一种坑。
        #   ⇒ 这里显式注入，**让本地 CI 与 CI 完全同口径**。
        #
        # ⚠️ **这个值安全吗**
        #   只用于 CI/本地 CI 容器的签名自洽性检查，
        #   不对应任何真实凭据、不出网、不进生产。
        "AUTH_TOKEN_SECRET": "ci-test-only-secret-not-a-real-credential-0123456789",
    }
    return [
        Check(
            "API 契约校验（严格模式）",
            [PYTHON, "backend/scripts/check_api_contract.py", "--strict"],
            REPO_ROOT,
            env,
        ),
        Check(
            # ⚠️ **生产姿态核对（B4）** —— 与 CI 工作流同一步骤。
            #    CI 上没有 backend/.env ⇒ 走代码默认值 ⇒ 必然通过；
            #    真正的价值在**部署环境**跑同一个脚本。
            "生产姿态核对（登录入口）",
            [PYTHON, "backend/scripts/check_production_posture.py"],
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
            # ⚠️ **绩效闭环 E2E（CI-5 阶段 3）** —— 真实库跑完整条链路。
            #    ⚠️ **为什么单元测试不够**
            #    真实库里曾出现 23 个考核任务里 **22 个 snapshot_id 为 NULL**、
            #    21 条记录卡在 manager_review_submitted、score 表一条都没有
            #    ⇒ **代码全绿，业务却断在中间**。
            #    ⇒ 单元测试全是「内存 SQLite + dependency_overrides」，
            #    覆盖不到事务提交、真实路由装配、跨表落库这些环节。
            #    ⇒ 这道检查跑的是**完整 9 步闭环**（发起 → 填报 → 评分 →
            #    面谈 → 确认 → 定稿 → 归档），每步都断言**库内状态**。
            #
            # ⚠️ **必须放在 Alembic 迁移之后** —— 它要往真实库里建表数据，
            #    schema 还没建好会直接报表不存在。
            "绩效闭环 E2E（真实库跑完整链路）",
            [PYTHON, "backend/scripts/verify_performance_closed_loop_e2e.py"],
            REPO_ROOT,
            env,
        ),
        Check(
            # ⚠️ **绩效异议分支 E2E（CI-5 阶段 3）** —— 验「回退 + 重算」这条路径。
            #    ⚠️ **为什么正向闭环脚本不够**
            #    顺利路径（填报 → 评分 → 面谈 → 确认 → 定稿 → 归档）里**没有回退动作**。
            #    而员工提异议后链路会**绕回去**：
            #        dispute → reopen（**删 score 行**、**清 confirmed_rating**、
            #                             **旧分数存进 previous_results**）
            #              → 主管重新评分 → 面谈 → 确认 → 定稿
            #    ⇒ 这条路径上有 4 个「删除/回退」动作，任一出错都会**静默丢分**。
            #    ⇒ 尤其 `review_score_version` 是 **append-only** 的，
            #      删 score 会不会连带触发历史表保护？必须真跑一次才知道。
            #
            # ⚠️ **同样必须放在 Alembic 迁移之后**。
            "绩效异议分支 E2E（真实库跑回退链路）",
            [PYTHON, "backend/scripts/verify_performance_dispute_branch_e2e.py"],
            REPO_ROOT,
            env,
        ),
        Check(
            # ⚠️ **权限隔离 E2E（CI-5 阶段 3）** —— 验 V1–V8。
            #    ⚠️ **为什么必须单独一个脚本**
            #    「管理员不是万能模拟账号」这条规则横跨**两个板块**
            #    （剪辑 ``/v1/baidu/workload`` + 部门 ``/v1/dept/workload``）。
            #    ⚠️ **实测抓到的真实漂移（2026-11-25）**
            #    部门板块的 ``_scope_employee_ids`` 多写了一个
            #    ``or is_system_admin(db, user)``
            #    ⇒ **管理员在部门板块能看全公司，在剪辑板块不能**
            #    ⇒ 共享服务 ``pan_workload_scope`` 已经实现了正确口径，
            #      路由层却**又加了一次**管理员放行 ⇒ 覆盖了共享服务的口径。
            #    ⇒ 这正是「抽成共享服务」也会漂移的实例
            #      （抽出来 ≠ 大家都会用）。
            #    ⇒ 本脚本**同时打两个板块** ⇒ 行为不一致必挂。
            #
            # ⚠️ **同样必须放在 Alembic 迁移之后**。
            "权限隔离 E2E（V1–V8 · 两个板块口径一致）",
            [PYTHON, "backend/scripts/verify_permission_isolation_e2e.py"],
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
        Check(
            # ⚠️ CI-6：后端依赖安全扫描。
            #    ⚠️ **扫 lock 文件，不扫整个环境** ——
            #    扫环境会把 pip 自身的 CVE 也算进来（本机 pip 24.3.1 有 12 个），
            #    而 pip 不是项目依赖 ⇒ 噪音淹没信号。
            "后端依赖安全扫描（pip-audit）",
            [
                PYTHON,
                "-m",
                "pip_audit",
                "-r",
                "backend/requirements.lock.txt",
                "--progress-spinner",
                "off",
                "--desc",
                "off",
            ],
            REPO_ROOT,
            # ⚠️ **Windows 必须设 PYTHONUTF8=1** ——
            #    pip-audit 按系统默认编码读文件，本机是 GBK，
            #    读 UTF-8 的 requirements.lock.txt 会抛 UnicodeDecodeError。
            #    CI 是 Ubuntu（UTF-8）所以没这问题，但本地脚本会踩。
            {"PYTHONUTF8": "1"},
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
            # ⚠️ CI-6：依赖安全扫描。
            #    门槛是 high —— moderate 及以下**故意不阻断**
            #    （剩 3 个 moderate 全在 vue-tsc → minimatch 链上，
            #     修它要破坏性升级 vue-tsc）。
            "前端依赖安全扫描（npm audit，门槛 high）",
            [NPM, "audit", "--audit-level=high"],
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
        Check(
            # ⚠️ CI-5 阶段 1：组件测试（真实挂载 + 交互断言）。
            #    与上面那条**并存** —— vitest 有 SFC 编译能力，
            #    ``node --test`` 没有 ⇒ 组件测试只能跑在 vitest 上。
            "前端组件测试（vitest · 真实挂载）",
            [NPM, "run", "test:component"],
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
