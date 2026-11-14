/**
 * 前端测试 —— 基线感知运行器。
 *
 * 为什么需要它？
 * --------------
 * 仓库里存在 10 个**历史遗留**的失败测试（详见 tests-baseline.json）。
 * 如果 CI 直接跑 `npm test` 并按退出码判定，这 10 个历史失败会让 CI 永远红灯，
 * 久而久之团队就会开始忽略 CI —— 这是最坏的结果。
 *
 * 如果 CI 直接忽略这 10 个失败，又会允许技术债**静默扩大**：
 * 今天是 10 个失败，明天多 1 个新失败没人会发现。
 *
 * 本运行器采取「基线锁定」策略：
 *   - 失败集合 ⊆ 基线集合  → 绿（没有引入新问题）
 *   - 出现基线之外的新失败 → 红（技术债扩大，立即拦截）
 *   - 基线里的失败被修复了 → 绿，并提示可以更新基线（鼓励持续还债）
 *
 * 用法：
 *   node scripts/run-tests-with-baseline.mjs                 # 校验
 *   node scripts/run-tests-with-baseline.mjs --update       # 重新生成基线
 *
 * 退出码：
 *   0 = 通过（失败未超基线）
 *   1 = 失败（出现基线之外的新失败，或测试运行器本身崩溃）
 */

import { spawn } from "node:child_process"
import { readdir, readFile, writeFile } from "node:fs/promises"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = dirname(fileURLToPath(import.meta.url))
const FRONTEND_ROOT = join(__dirname, "..")
const BASELINE_FILE = join(FRONTEND_ROOT, "tests-baseline.json")

const UPDATE_MODE = process.argv.includes("--update")

/** node --test 的失败标记行：`✖ <测试名> (<耗时>ms)` */
const FAIL_LINE_RE = /^✖\s+(.+?)\s+\(\d+(?:\.\d+)?ms\)\s*$/
/** 汇总行：`ℹ fail 10` */
const FAIL_COUNT_RE = /^ℹ\s+fail\s+(\d+)\s*$/
/** 汇总行：`ℹ tests 176` */
const TEST_COUNT_RE = /^ℹ\s+tests\s+(\d+)\s*$/
/** 汇总行：`ℹ pass 166` */
const PASS_COUNT_RE = /^ℹ\s+pass\s+(\d+)\s*$/

/**
 * 从 node 测试输出里提取失败测试名。
 *
 * ⚠️ node --test 会把失败列表打印**两遍**：
 *    一遍是逐条 `✖ name (12ms)`，另一遍是结尾的 `✖ failing tests:` 汇总段。
 *    这里用 Set 去重，保证每个失败只算一次。
 */
function parseFailures(output) {
  const failures = new Set()
  for (const line of output.split(/\r?\n/)) {
    const match = line.match(FAIL_LINE_RE)
    if (match) {
      failures.add(match[1].trim())
    }
  }
  return [...failures]
}

function parseCounts(output) {
  const counts = { tests: 0, pass: 0, fail: 0 }
  for (const line of output.split(/\r?\n/)) {
    let m = line.match(TEST_COUNT_RE)
    if (m) {
      counts.tests = Number(m[1])
      continue
    }
    m = line.match(PASS_COUNT_RE)
    if (m) {
      counts.pass = Number(m[1])
      continue
    }
    m = line.match(FAIL_COUNT_RE)
    if (m) {
      counts.fail = Number(m[1])
    }
  }
  return counts
}

/**
 * 列出所有测试文件。
 *
 * ⚠️ 不能用 shell 通配符 `tests/*.test.ts`：
 *    - Windows 下 `shell: true` 会把参数原样拼接，glob 不会被展开；
 *    - 显式列文件在 Windows / Linux 上行为一致。
 */
async function listTestFiles() {
  const dir = join(FRONTEND_ROOT, "tests")
  const entries = await readdir(dir, { withFileTypes: true })
  return entries
    .filter((e) => e.isFile() && e.name.endsWith(".test.ts"))
    .map((e) => join("tests", e.name))
    .sort()
}

/** 跑 node --test，把 stdout/stderr 收集起来 */
async function runTests() {
  const files = await listTestFiles()
  if (files.length === 0) {
    throw new Error("未找到任何测试文件（frontend/tests/*.test.ts）")
  }

  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      ["--experimental-strip-types", "--test", ...files],
      {
        cwd: FRONTEND_ROOT,
        // ⚠️ 不用 shell —— 显式传文件列表，避免 Windows 下的 glob 不展开问题
        shell: false,
      },
    )

    let output = ""
    child.stdout?.on("data", (d) => {
      output += d.toString()
    })
    child.stderr?.on("data", (d) => {
      output += d.toString()
    })

    child.on("error", reject)
    child.on("close", () => resolve(output))
  })
}

async function loadBaseline() {
  try {
    const raw = await readFile(BASELINE_FILE, "utf8")
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed.knownFailures) ? parsed.knownFailures : []
  } catch {
    return []
  }
}

async function main() {
  console.log("▶ 运行前端单元测试（node --test）…\n")
  const output = await runTests()
  const failures = parseFailures(output)
  const counts = parseCounts(output)

  console.log(
    `  测试总数: ${counts.tests}  通过: ${counts.pass}  失败: ${counts.fail}`,
  )

  if (UPDATE_MODE) {
    const payload = {
      description:
        "前端历史遗留失败测试清单。CI 用它做基线锁定：出现清单之外的新失败即阻断。",
      updatedAt: new Date().toISOString(),
      knownFailures: failures.sort(),
    }
    await writeFile(BASELINE_FILE, `${JSON.stringify(payload, null, 2)}\n`, "utf8")
    console.log(`\n✔ 已更新基线：${BASELINE_FILE}（${failures.length} 条）`)
    process.exit(0)
  }

  const baseline = await loadBaseline()
  const baselineSet = new Set(baseline)
  const currentSet = new Set(failures)

  const newFailures = failures.filter((name) => !baselineSet.has(name))
  const fixedFailures = baseline.filter((name) => !currentSet.has(name))

  console.log(`\n基线失败数: ${baseline.length}    当前失败数: ${failures.length}`)

  if (newFailures.length > 0) {
    console.error(`\n✖ 出现 ${newFailures.length} 个基线之外的新失败（CI 阻断）：`)
    for (const name of newFailures) {
      console.error(`   - ${name}`)
    }
    console.error(
      "\n处理方式：\n" +
        "  1) 修复该问题 → 重新跑 CI；\n" +
        "  2) 若确认为可接受的历史问题 → 执行 " +
        "`node scripts/run-tests-with-baseline.mjs --update` 更新基线。",
    )
    process.exit(1)
  }

  if (fixedFailures.length > 0) {
    console.log(
      `\n✔ 比基线少 ${fixedFailures.length} 个失败（已修复，建议更新基线）：`,
    )
    for (const name of fixedFailures) {
      console.log(`   - ${name}`)
    }
    console.log(
      "  更新基线命令：node scripts/run-tests-with-baseline.mjs --update",
    )
  }

  console.log("\n✔ 前端测试通过（失败未超基线）")
  process.exit(0)
}

main().catch((err) => {
  console.error("✖ 测试运行器异常：", err)
  process.exit(1)
})
