import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// ⚠️ 本文件锁定「角色码 → 中文标签」的映射。
//
// 背景（2026-11-26 修复的真实缺陷）：
//   `profilePresentation.ts` 的 `BUILT_IN_ROLE_LABELS` 以前写的是
//   `SUPER_ADMIN` / `DEPT_LEADER` / `EMPLOYEE` 等**全大写自造码**，
//   而后端 `roles` 字段发的是 `admin` / `department_leader` / `employee`
//   ⇒ key 完全对不上 ⇒ `roleLabel()` 匹配不到就**原样返回英文**
//   ⇒ 用户在个人中心看到「admin department_leader employee hr manager」。
//
// ⚠️ **为什么这里用「读源码 + 正则」而不是直接 import 函数**：
//   这份映射表的正确性**取决于它与后端的一致性**，而不是它自身的逻辑。
//   所以断言的重点是：
//     ① 每个后端真实角色码都有中文标签（不漏译）；
//     ② 没有自造的历史残留码（SUPER_ADMIN 之类）；
//     ③ 没有「超级管理员」这种误导词。
//   这些都是**源码形状**问题，源码断言最直接、也不会假绿。

const backendAuth = readFileSync(
  fileURLToPath(new URL('../../backend/app/routes/auth.py', import.meta.url)),
  'utf8',
)

const presentation = readFileSync(
  fileURLToPath(new URL('../src/views/profile/profilePresentation.ts', import.meta.url)),
  'utf8',
)

// ─────────────────────────────────────────────────────────────
// 从后端权威来源提取角色码
// ─────────────────────────────────────────────────────────────

/**
 * 提取后端 `ROLE_UI_PERMISSIONS` 的 key 列表。
 *
 * ⚠️ 只认**顶层 key**（4 空格缩进的 `"xxx": [`）——
 * 嵌套的权限位字符串（如 `"dashboard:view"`）不是角色码。
 */
function backendRoleCodes(): string[] {
  const start = backendAuth.indexOf('ROLE_UI_PERMISSIONS')
  assert.ok(start > 0, '后端应存在 ROLE_UI_PERMISSIONS')

  const block = backendAuth.slice(start)
  const codes: string[] = []
  const re = /^ {4}"([a-z_]+)": \[/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    codes.push(m[1])
  }
  return codes
}

/**
 * 提取前端映射表的 key。
 */
function frontendRoleCodes(): string[] {
  const start = presentation.indexOf('BUILT_IN_ROLE_LABELS')
  assert.ok(start > 0, '前端应存在 BUILT_IN_ROLE_LABELS')

  const block = presentation.slice(start)
  const codes: string[] = []
  const re = /^\s{2}([A-Za-z_]+): '/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    codes.push(m[1])
  }
  return codes
}

// ─────────────────────────────────────────────────────────────
// 1. 映射表与后端一致
// ─────────────────────────────────────────────────────────────

test('后端角色码集合与前端映射表 key 完全一致', () => {
  const backend = backendRoleCodes().sort()
  const frontend = frontendRoleCodes().sort()

  assert.ok(backend.length > 0, '应能提取到后端角色码')
  assert.deepEqual(
    frontend,
    backend,
    `前端角色标签的 key 必须与后端角色码完全一致。\n` +
      `  后端: ${backend.join(', ')}\n` +
      `  前端: ${frontend.join(', ')}`,
  )
})

test('后端真实存在的每个角色码都有中文标签（不漏译）', () => {
  const presentationBlock = presentation.slice(
    presentation.indexOf('BUILT_IN_ROLE_LABELS'),
  )

  for (const code of backendRoleCodes()) {
    const re = new RegExp(`^\\s{2}${code}: '([^']+)'`, 'm')
    const m = re.exec(presentationBlock)
    assert.ok(m, `角色码 "${code}" 缺少中文标签`)
    assert.notEqual(
      m[1],
      code,
      `角色码 "${code}" 的标签不能原样返回英文（那正是本次要修的 bug）`,
    )
  }
})

// ─────────────────────────────────────────────────────────────
// 2. 历史残留码已清除
// ─────────────────────────────────────────────────────────────

test('不含历史自造角色码（SUPER_ADMIN 等）', () => {
  const frontend = frontendRoleCodes()

  for (const legacy of [
    'SUPER_ADMIN',
    'SYS_ADMIN',
    'DEPT_LEADER',
    'DEPT_OWNER',
    'DIRECT_LEADER',
    'PROJECT_MANAGER',
    'REVIEWER',
    'EMPLOYEE',
  ]) {
    assert.ok(
      !frontend.includes(legacy),
      `不应残留历史角色码 "${legacy}" —— 后端从未发送过它`,
    )
  }
})

test('不含「超级管理员」这个误导词', () => {
  // ⚠️ 只查**字符串字面量**，不查注释 ——
  //   注释里提到「超级管理员」是为了解释「为什么删掉它」。
  const literals = [...presentation.matchAll(/'([^'\n]*)'/g)].map((m) => m[1])
  assert.ok(
    !literals.some((text) => text.includes('超级管理员')),
    '后端不存在「超级管理员」角色 —— 管理员是公司内真实员工，' +
      '只是被授予系统配置权限，不是万能/可伪装身份的账号。' +
      '保留这个词会误导用户以为存在后门角色。',
  )
})

test('系统管理员的正确措辞是「系统管理员」', () => {
  assert.ok(
    /^\s{2}admin: '系统管理员'/m.test(presentation),
    'admin 角色码应显示为「系统管理员」',
  )
})

// ─────────────────────────────────────────────────────────────
// 3. 未知角色码的行为
// ─────────────────────────────────────────────────────────────

test('未知角色码原样返回（不返回含糊的占位文案）', () => {
  // `roleLabel` 的兜底是 `BUILT_IN_ROLE_LABELS[code] || code`。
  const fn = presentation.slice(presentation.indexOf('export function roleLabel'))
  assert.ok(
    /\|\|\s*code/.test(fn),
    '未知角色码应原样返回，便于排查后端新增角色后前端未跟进的情况',
  )

  // ⚠️ 只查**字符串字面量**，不查注释 ——
  //   注释里出现「未知角色」是为了解释「为什么不用它」，不该被判失败。
  const literals = [...presentation.matchAll(/'([^'\n]*)'/g)].map((m) => m[1])
  assert.ok(
    !literals.includes('未知角色'),
    '不应使用含糊的「未知角色」占位',
  )
})

// ─────────────────────────────────────────────────────────────
// 4. 用户可见效果
// ─────────────────────────────────────────────────────────────

test('林雅静（真实员工·HR·主管·系统管理员）的中文标签可读', () => {
  // 她的真实角色组合（实测自 perf.db）。
  const roles = ['admin', 'department_leader', 'employee', 'hr', 'manager']
  const expected = ['系统管理员', '部门负责人', '员工', 'HR', '主管']

  const presentationBlock = presentation.slice(
    presentation.indexOf('BUILT_IN_ROLE_LABELS'),
  )
  const labels = roles.map((code) => {
    const m = new RegExp(`^\\s{2}${code}: '([^']+)'`, 'm').exec(presentationBlock)
    assert.ok(m, `角色码 "${code}" 缺少中文标签`)
    return m[1]
  })

  assert.deepEqual(labels, expected)
  // 不应出现任何英文小写角色码。
  for (const label of labels) {
    assert.ok(
      !/^[a-z_]+$/.test(label),
      `标签 "${label}" 仍是英文 —— 用户会看到一串英文角色码`,
    )
  }
})
