// 清理 + 包装脚本：
//  1) 把 `const A = B2` 形式的重命名别名折叠掉，直接使用 import 里的名字
//  2) 把所有后台路由（除 /login、/change-password）用 withAppShell(...) 包装
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const target = resolve(__dirname, '../src/router/index.ts')
let source = readFileSync(target, 'utf8')

// ---------- 1) 折叠别名 ----------
const aliasRe = /^const\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\d*\s*$/gm
const aliases = [...source.matchAll(aliasRe)].map((m) => ({ alias: m[1], imported: m[2] }))

for (const { alias, imported } of aliases) {
  // import <imported><digits> from '...'  →  import <alias> from '...'
  source = source.replace(
    new RegExp(`^import\\s+${imported}\\d*\\s+from\\s+('[^']+')\\s*$`, 'm'),
    `import ${alias} from $1`,
  )
  // 删除 const <alias> = <imported><digits>
  source = source.replace(
    new RegExp(`^const\\s+${alias}\\s*=\\s*${imported}\\d*\\s*\\r?\\n`, 'gm'),
    '',
  )
}
console.log(`已折叠 ${aliases.length} 个别名`)

// ---------- 2) 包装后台路由 ----------
const STANDALONE = new Set(['/login', '/change-password'])

const lines = source.split('\n')
const out = []
let wrapped = 0

for (let i = 0; i < lines.length; i++) {
  const line = lines[i]
  const pathMatch = line.match(/^\s*\{ path: '([^']+)',/)

  if (pathMatch && !STANDALONE.has(pathMatch[1])) {
    // 单行形式：{ path: 'x', component: Foo, ... }
    const single = line.match(/^(\s*\{ path: '[^']+', component: )([A-Za-z_$][\w$]*)(,.*)$/)
    if (single && single[2] !== 'NoAccess') {
      out.push(`${single[1]}withAppShell(${single[2]})${single[3]}`)
      wrapped++
      continue
    }
    // 多行形式：component 在后续行
    const next = lines[i + 1]
    const multi = next && next.match(/^(\s*component: )([A-Za-z_$][\w$]*)(,.*)$/)
    if (multi && multi[2] !== 'NoAccess') {
      out.push(line)
      out.push(`${multi[1]}withAppShell(${multi[2]})${multi[3]}`)
      wrapped++
      i++
      continue
    }
  }
  out.push(line)
}

source = out.join('\n')
source = source.replace(/\n{3,}/g, '\n\n')

writeFileSync(target, source, 'utf8')
console.log(`已用 withAppShell 包装 ${wrapped} 条后台路由`)
