import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const loginView = readFileSync(
  fileURLToPath(new URL('../src/views/Login.vue', import.meta.url)),
  'utf8',
)

// ═══════════════════════════════════════════════════════════════════
// BUG-R6-32：Login.vue 不得把明文密码写进 localStorage
// ═══════════════════════════════════════════════════════════════════

test('登录页不把密码写入任何持久化存储', () => {
  // 逐个取出 localStorage.setItem 的**完整参数**，断言里面没有 password 字段。
  // 用非贪婪匹配到 `))` 结束 —— setItem(KEY, JSON.stringify({ ... }))
  const storageWrites = loginView.match(/localStorage\.setItem\([\s\S]*?\n\s*\}\)\)/g) || []
  assert.ok(storageWrites.length > 0, '应至少有一处 localStorage.setItem')
  for (const write of storageWrites) {
    assert.doesNotMatch(
      write,
      /password/i,
      `localStorage 写入内容里不得出现 password: ${write}`,
    )
  }
  // 全文件兜底：**setItem / getItem** 不得涉及 password。
  // （removeItem(LEGACY_PASSWORD_MEMORY_KEY) 是「删旧数据」，
  //   常量名里带 PASSWORD 是刻意的，不算违规。）
  assert.doesNotMatch(
    loginView,
    /localStorage\.(setItem|getItem)\([^)]*password/i,
    '不得用 setItem/getItem 读写任何含 password 的键',
  )
  // 而且：除遗留清理外，不得有任何 localStorage 键名带 password。
  const storageKeys = loginView.match(/localStorage\.(?:setItem|getItem)\(\s*[A-Za-z_][A-Za-z0-9_]*/g) || []
  for (const call of storageKeys) {
    assert.doesNotMatch(
      call,
      /PASSWORD/i,
      `读写用的 localStorage 键名不得含 PASSWORD: ${call}`,
    )
  }
})

test('登录页只记用户名，不记密码', () => {
  // 记住的 key 名字里不能有 password。
  // ⚠️ 但旧 key 仍会作为「待清理的遗留 key」出现（LEGACY_PASSWORD_MEMORY_KEY），
  //    那是**只删不读**的迁移常量，不是存储位置 —— 所以只断言它不被用于读取。
  assert.match(loginView, /planning_login_username_memory/)

  // 旧 key 只能出现在 removeItem 里，绝不能出现在 getItem / setItem 里。
  assert.doesNotMatch(
    loginView,
    /localStorage\.getItem\(LEGACY_PASSWORD_MEMORY_KEY\)/,
    '旧 key 只能被删除，绝不能被读取',
  )
  assert.doesNotMatch(
    loginView,
    /localStorage\.setItem\(LEGACY_PASSWORD_MEMORY_KEY/,
    '旧 key 绝不能被写入',
  )
  assert.match(loginView, /localStorage\.removeItem\(LEGACY_PASSWORD_MEMORY_KEY\)/)
})

test('旧版明文密码记录在加载时被主动清除', () => {
  // 迁移逻辑必须存在：读取到旧 key 就删掉
  assert.match(loginView, /LEGACY_PASSWORD_MEMORY_KEY/)
  assert.match(loginView, /localStorage\.removeItem\(LEGACY_PASSWORD_MEMORY_KEY\)/)
  // 且必须在加载入口被调用
  assert.match(loginView, /onMounted\(loadRememberedUsername\)/)
  assert.match(loginView, /function loadRememberedUsername\(\)[\s\S]*purgeLegacyPasswordMemory\(\)/)
})

test('加载记住的账号时不回填密码框', () => {
  const loadFn = loginView.match(/function loadRememberedUsername\(\)\s*\{[\s\S]*?\n\}/)
  assert.ok(loadFn, '应能找到 loadRememberedUsername 函数')
  assert.match(loadFn[0], /form\.username = saved\.username/)
  assert.match(loadFn[0], /form\.password = ''/)
  // 不得出现任何从 saved 里取密码的写法
  assert.doesNotMatch(loadFn[0], /saved\.password/)
  assert.doesNotMatch(loadFn[0], /form\.password = saved/)
})

test('记住的复选框文案是「记住账号」而不是「记住密码」', () => {
  assert.match(loginView, /记住账号/)
  assert.doesNotMatch(loginView, />记住密码</)
})

test('记住用户名有效期 30 天，不再是 7 天', () => {
  assert.match(loginView, /REMEMBER_USERNAME_TTL = 30 \* 24 \* 60 \* 60 \* 1000/)
  assert.doesNotMatch(loginView, /REMEMBER_PASSWORD_TTL/)
})
