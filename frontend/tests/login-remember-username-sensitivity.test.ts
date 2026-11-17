import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// ⚠️ 本文件验证「测试本身是否够敏感」——
//    用一份**临时的、内存里的**旧版片段做对照，
//    绝不去修改真实的 Login.vue（不允许再把明文密码写回源码）。
//    目的：证明回归测试真的能抓到「又存了密码」这种回退。

const loginView = readFileSync(
  fileURLToPath(new URL('../src/views/Login.vue', import.meta.url)),
  'utf8',
)

function findStorageWrites(source: string): string[] {
  return source.match(/localStorage\.setItem\([\s\S]*?\n\s*\}\)\)/g) || []
}

test('当前 Login.vue 通过所有安全断言', () => {
  for (const write of findStorageWrites(loginView)) {
    assert.doesNotMatch(write, /password/i)
  }
  assert.doesNotMatch(
    loginView,
    /localStorage\.(setItem|getItem)\([^)]*password/i,
  )
  const storageKeys = loginView.match(/localStorage\.(?:setItem|getItem)\(\s*[A-Za-z_][A-Za-z0-9_]*/g) || []
  for (const call of storageKeys) {
    assert.doesNotMatch(call, /PASSWORD/i)
  }
})

test('断言对「回退到旧写法」确实会失败（测试敏感度自检）', () => {
  // 构造一份「假设有人又把密码存回去」的源码片段（只在内存里）。
  const regressed = loginView.replace(
    /username: form\.username,\n(\s*)expiresAt/,
    'username: form.username,\n$1password: form.password,\n$1expiresAt',
  )
  assert.notEqual(regressed, loginView, '构造的回归样本应与当前源码不同')

  // 同样的断言必须抓到这个回归。
  let caught = false
  for (const write of findStorageWrites(regressed)) {
    if (/password/i.test(write)) {
      caught = true
      break
    }
  }
  if (!caught) {
    assert.doesNotMatch(
      regressed,
      /localStorage\.(setItem|getItem)\([^)]*password/i,
    )
  }
  assert.ok(caught, '回归样本应被断言抓到')
})
