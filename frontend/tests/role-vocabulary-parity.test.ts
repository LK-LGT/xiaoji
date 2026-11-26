import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// ⚠️ 本文件锁定「后端 ↔ 前端角色词表一致」（2026-11-26 建立）。
//
// 背景：
//   后端历史上同时存在 **4 套**互相对不上的角色码：
//     ① `access_control.py`          → employee/manager/department_leader/hr/admin ✅
//     ② `system_views._role_names`   → 用了 `leader` 代替 `manager`，且漏了 `hr` ❌
//     ③ `system_views` 角色候选      → `id` 小写 / `code` 大写自造码，两套并存 ❌
//     ④ `app/permissions.py` RoleEnum → `department` / `reviewer`（后者根本不存在）❌
//   ⇒ 同一个人的角色在「个人中心」和「员工管理」里显示**不一样**。
//
// 现在后端收敛到唯一事实源 `backend/app/services/role_vocabulary.py`，
// 前端镜像在 `frontend/src/views/profile/profilePresentation.ts`。
//
// ⚠️ **为什么用「读源码 + 正则」而不是直接 import**：
//   后端是 Python、前端是 TS，跨语言没法 import ⇒ 只能读源码比对。
//   这也正好断言了「前后端各写一份」这个**结构性事实**：
//   只要两边都还在，就必然要靠测试守住它们一致。

const backendVocabulary = readFileSync(
  fileURLToPath(
    new URL('../../backend/app/services/role_vocabulary.py', import.meta.url),
  ),
  'utf8',
)
const presentation = readFileSync(
  fileURLToPath(
    new URL('../src/views/profile/profilePresentation.ts', import.meta.url),
  ),
  'utf8',
)
const accessControl = readFileSync(
  fileURLToPath(
    new URL('../../backend/app/services/access_control.py', import.meta.url),
  ),
  'utf8',
)
const systemViews = readFileSync(
  fileURLToPath(
    new URL('../../backend/app/services/system_views.py', import.meta.url),
  ),
  'utf8',
)

// ─────────────────────────────────────────────────────────────
// 提取工具
// ─────────────────────────────────────────────────────────────

/**
 * 提取后端 ``Role`` 枚举的**成员名 → 值**（小写角色码）。
 */
function backendRoleEnum(): Record<string, string> {
  const start = backendVocabulary.indexOf('class Role(StrEnum)')
  assert.ok(start > 0, '后端应存在 Role 枚举')

  const block = backendVocabulary.slice(start)
  const out: Record<string, string> = {}
  const re = /^\s{4}([A-Z_]+)\s*=\s*"([a-z_]+)"/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    out[m[1]] = m[2]
  }
  return out
}

/**
 * 提取后端 ``ROLE_LABELS`` 的**角色码 → 中文标签**。
 *
 * ⚠️ 源码里写的是 ``Role.EMPLOYEE: "员工"``（枚举成员做 key），
 *    这里要**解析成实际的值**（``employee``），才能与前端比对。
 */
function backendRoleLabels(): Record<string, string> {
  const start = backendVocabulary.indexOf('ROLE_LABELS')
  assert.ok(start > 0, '后端应存在 ROLE_LABELS')

  const enumMap = backendRoleEnum()
  const block = backendVocabulary.slice(start)
  const out: Record<string, string> = {}
  const re = /^\s{4}(?:Role\.([A-Z_]+)|"([a-z_]+)")\s*:\s*"([^"]+)"/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    const code = m[1] ? enumMap[m[1]] : m[2]
    assert.ok(code, `ROLE_LABELS 里的 key 无法解析成角色码：${m[0].trim()}`)
    out[code] = m[3]
  }
  return out
}

/**
 * 提取前端 ``BUILT_IN_ROLE_LABELS`` 的 key → value。
 */
function frontendRoleLabels(): Record<string, string> {
  const start = presentation.indexOf('BUILT_IN_ROLE_LABELS')
  assert.ok(start > 0, '前端应存在 BUILT_IN_ROLE_LABELS')

  const block = presentation.slice(start)
  const out: Record<string, string> = {}
  const re = /^\s{2}([a-z_]+):\s*'([^']+)'/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    out[m[1]] = m[2]
  }
  return out
}

/**
 * 剥掉 Python 注释 —— 只留**真正的代码**。
 *
 * ⚠️ 为什么必须剥：注释里经常要**引用**问题码来说明历史
 *（例如「过去这里手写 ``roles.add("manager")``」），
 * 那不是「代码里在用这个码」。
 */
function stripPythonComments(source: string): string {
  return source
    .split('\n')
    .map((line) => {
      // 去掉行尾注释（不处理字符串内的 `#`，本仓库代码里没有这种写法）。
      const hash = line.indexOf('#')
      return hash === -1 ? line : line.slice(0, hash)
    })
    .join('\n')
}

/**
 * 取源码中某个区块的**代码部分**（剥注释 + 去掉 docstring 干扰）。
 */
function codeBlock(source: string, marker: string, until?: string): string {
  const start = source.indexOf(marker)
  assert.ok(start > 0, `源码应存在 ${marker}`)
  let block = source.slice(start)
  if (until) {
    const end = block.indexOf(until, 1)
    if (end > 0) block = block.slice(0, end)
  }
  return stripPythonComments(block)
}

// ─────────────────────────────────────────────────────────────
// 1. 前后端映射表逐项一致
// ─────────────────────────────────────────────────────────────

test('前端角色标签与后端 ROLE_LABELS 逐项一致（码与中文标签都要对）', () => {
  const backend = backendRoleLabels()
  const frontend = frontendRoleLabels()

  assert.ok(Object.keys(backend).length > 0, '应能提取到后端角色标签')
  assert.deepEqual(
    frontend,
    backend,
    '前后端角色标签必须完全一致 —— 不一致就会有一端显示英文或显示旧标签。\n' +
      `  后端: ${JSON.stringify(backend)}\n` +
      `  前端: ${JSON.stringify(frontend)}`,
  )
})

test('后端 Role 枚举与 ROLE_LABELS 的 key 一一对应', () => {
  const enumValues = Object.values(backendRoleEnum()).sort()
  const labelKeys = Object.keys(backendRoleLabels()).sort()

  assert.deepEqual(
    enumValues,
    labelKeys,
    'Role 枚举与 ROLE_LABELS 必须覆盖同一批角色码 —— 否则有一个角色没有中文标签。',
  )
})

// ─────────────────────────────────────────────────────────────
// 2. 后端内部不再各写一套
// ─────────────────────────────────────────────────────────────

test('access_control 不再手写 roles.add(...)（改由 derive_roles 统一推导）', () => {
  // ⚠️ 必须剥掉注释 —— 注释里会**引用**历史写法来说明「过去是怎么错的」。
  const code = stripPythonComments(accessControl)
  assert.doesNotMatch(
    code,
    /roles\.add\(/,
    'access_control 不应再手写 roles.add —— 角色推导已收敛到 derive_roles。',
  )
  assert.match(
    code,
    /derive_roles\(/,
    'access_control 应调用 role_vocabulary.derive_roles。',
  )
})

test('system_views 的角色推导复用 build_access_context（不再另写一份）', () => {
  const code = stripPythonComments(systemViews)
  assert.doesNotMatch(
    code,
    /names\.append\(/,
    'system_views 不应再自己拼角色列表 —— 那正是漂移的来源。',
  )
  assert.match(
    code,
    /build_access_context/,
    'system_views 应复用 access_control.build_access_context。',
  )
  assert.match(
    code,
    /role_choices\(\)/,
    'system_views 的角色候选应来自 role_vocabulary.role_choices()。',
  )
})

test('后端不再出现历史自造角色码（大写码 / leader / reviewer）', () => {
  // ⚠️ **排除 ``LEGACY_ROLE_ALIASES`` 归一表** ——
  //   那张表**故意**列着所有历史码，正是用来归一它们的。
  for (const source of [backendVocabulary, accessControl, systemViews]) {
    let code = stripPythonComments(source)
    if (source === backendVocabulary) {
      const aliasStart = code.indexOf('LEGACY_ROLE_ALIASES')
      assert.ok(aliasStart > 0, '后端应存在 LEGACY_ROLE_ALIASES')
      code = code.slice(0, aliasStart)
    }

    // ⚠️ 只查**代码**（引号里的字符串），不查注释 ——
    //   注释里提到这些码是为了解释「为什么归一它们」。
    const literals = [...code.matchAll(/["']([A-Za-z_]+)["']/g)].map((m) => m[1])
    for (const legacy of [
      'SUPER_ADMIN',
      'SYS_ADMIN',
      'DEPT_LEADER',
      'DEPT_OWNER',
      'EMPLOYEE',
      'DEPARTMENT_LEADER',
      'DIRECT_LEADER',
      'PROJECT_MANAGER',
    ]) {
      assert.ok(
        !literals.includes(legacy),
        `不应在代码里使用历史自造角色码 "${legacy}"。`,
      )
    }
    assert.ok(
      !literals.includes('reviewer'),
      '后端不存在「裁决评审员」角色 —— 它只存在于废弃的 app/permissions.py。',
    )
  }
})

// ─────────────────────────────────────────────────────────────
// 3. 归一规则正确
// ─────────────────────────────────────────────────────────────

test('历史码归一表覆盖所有已知遗留写法', () => {
  const start = backendVocabulary.indexOf('LEGACY_ROLE_ALIASES')
  assert.ok(start > 0, '后端应存在 LEGACY_ROLE_ALIASES')

  const block = backendVocabulary.slice(start)
  const aliases: Record<string, string> = {}
  const re = /^\s{4}"([^"]+)":\s*Role\.([A-Z_]+)/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    aliases[m[1]] = m[2]
  }

  // 关键历史码必须能归一到规范码。
  const expected: Record<string, string> = {
    SUPER_ADMIN: 'ADMIN',
    SYS_ADMIN: 'ADMIN',
    ADMIN: 'ADMIN',
    DEPT_LEADER: 'DEPARTMENT_LEADER',
    DEPT_OWNER: 'DEPARTMENT_LEADER',
    DEPARTMENT_LEADER: 'DEPARTMENT_LEADER',
    department: 'DEPARTMENT_LEADER',
    DIRECT_LEADER: 'MANAGER',
    TEAM_LEADER: 'MANAGER',
    PROJECT_MANAGER: 'MANAGER',
    LEADER: 'MANAGER',
    MANAGER: 'MANAGER',
    EMPLOYEE: 'EMPLOYEE',
  }
  for (const [legacy, target] of Object.entries(expected)) {
    assert.equal(
      aliases[legacy],
      target,
      `历史码 "${legacy}" 应归一到 Role.${target}`,
    )
  }

  // ⚠️ 「reviewer」不应被归一 —— 归一它等于凭空造一个角色。
  assert.ok(
    !('REVIEWER' in aliases) && !('reviewer' in aliases),
    '「reviewer」不是后端角色，不该出现在归一表里。',
  )
})

test('归一表的每个目标角色都真实存在', () => {
  const enumValues = new Set(Object.values(backendRoleEnum()))
  const start = backendVocabulary.indexOf('LEGACY_ROLE_ALIASES')
  const block = backendVocabulary.slice(start)
  const re = /^\s{4}"([^"]+)":\s*Role\.([A-Z_]+)/gm
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) {
    const roleValue = backendRoleEnum()[m[2]]
    assert.ok(
      roleValue && enumValues.has(roleValue),
      `归一表指向 Role.${m[2]}，但该角色不在 Role 枚举里` +
        `（枚举值: ${[...enumValues].join(', ')}）。`,
    )
  }
})

// ─────────────────────────────────────────────────────────────
// 4. 用户可见效果
// ─────────────────────────────────────────────────────────────

test('林雅静（真实员工·HR·主管·部门负责人·系统管理员）的中文标签可读', () => {
  const roles = ['admin', 'department_leader', 'employee', 'hr', 'manager']
  const expected = ['系统管理员', '部门负责人', '员工', 'HR', '主管']

  const frontend = frontendRoleLabels()
  const labels = roles.map((code) => {
    const label = frontend[code]
    assert.ok(label, `角色码 "${code}" 缺少中文标签`)
    return label
  })

  assert.deepEqual(labels, expected)
  for (const label of labels) {
    assert.ok(
      !/^[a-z_]+$/.test(label),
      `标签 "${label}" 仍是英文 —— 用户会看到一串英文角色码`,
    )
  }
})

test('「超级管理员」这个误导词不出现', () => {
  // ⚠️ 只查**字符串字面量**，不查注释 —— 注释里提到它是为了解释「为什么不用」。
  const literals = [...presentation.matchAll(/'([^'\n]*)'/g)].map((m) => m[1])
  assert.ok(
    !literals.some((text) => text.includes('超级管理员')),
    '后端不存在「超级管理员」角色 —— 管理员是公司内真实员工，' +
      '只是被授予系统配置权限，不是万能/可伪装身份的账号。',
  )
})
