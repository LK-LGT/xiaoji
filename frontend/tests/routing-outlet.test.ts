import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const routerSource = readFileSync(
  fileURLToPath(new URL('../src/router/index.ts', import.meta.url)),
  'utf8',
)
const appSource = readFileSync(
  fileURLToPath(new URL('../src/App.vue', import.meta.url)),
  'utf8',
)
const appShellSource = readFileSync(
  fileURLToPath(new URL('../src/components/AppShell.vue', import.meta.url)),
  'utf8',
)
const viteConfigSource = readFileSync(
  fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  'utf8',
)

test('所有路由组件使用静态 import，不存在懒加载 import()', () => {
  // 止血方案：动态 import() 在本项目的 Vite/HMR 组合下可能返回 default=undefined，
  // 导致 RouterView 渲染出空注释。全部改为顶层静态 import。
  assert.doesNotMatch(routerSource, /\(\)\s*=>\s*import\(/, '路由中不应再有 () => import(...)')
  assert.doesNotMatch(routerSource, /\bimport\s*\(/, '路由中不应再有任意 import() 调用')

  const staticViewImports = routerSource.match(/from\s*['"][^'"]*views\/[^'"]+['"]/g) || []
  assert.ok(
    staticViewImports.length >= 50,
    `应至少静态引入 50 个页面组件，实际 ${staticViewImports.length}`,
  )

  // 每个静态引入都必须是独立的组件名（没有重名带 2 的后缀残留）
  assert.doesNotMatch(routerSource, /\w+2\b/, '不应残留 X2 之类的重命名别名')
})

test('后台页面统一挂在 AppShell 布局路由的 children 下', () => {
  // 嵌套布局结构：depth=0 是 AppShell，depth=1 才是页面组件。
  // 若页面组件仍挂在顶层，而 AppShell 内部又只有 router-view，
  // 那个 router-view 会去匹配 depth=1 —— 匹配不到记录就渲染空注释，
  // 表现为「点击任意左侧菜单，右侧内容区全部空白」。
  assert.match(routerSource, /component:\s*AppShell/, '应存在以 AppShell 为布局组件的路由')
  assert.match(routerSource, /children\s*:\s*\[/, 'AppShell 路由应包含 children')

  // 登录 / 修改密码是独立路由，不套后台框架
  assert.match(routerSource, /path:\s*'\/login'[\s\S]{0,80}meta:\s*\{\s*public:\s*true/, '/login 应为 public 独立路由')
  assert.match(routerSource, /path:\s*'\/change-password'/, '/change-password 应存在')

  // 后台页面不能再以顶层路由出现
  const topLevelSystemRoute = /^\s{4}\{ path: '\/system\/dashboard'/m.test(routerSource)
  assert.equal(topLevelSystemRoute, false, '后台页面不应再作为顶层路由')
})

test('App.vue 只保留一个顶层 router-view，不做 v-if 分流', () => {
  // App 是根组件，其渲染函数不会因路由变化重新执行，
  // 在这里用 v-if="$route.meta.public" 分流两套视图会导致退出登录后后台框架残留。
  // 只检查真实模板（去掉注释后再断言），避免匹配到注释里的示例文字
  const appTemplate = appSource.replace(/<!--[\s\S]*?-->/g, '')
  assert.match(appTemplate, /<router-view\s*\/>/, 'App.vue 应有顶层 router-view')
  assert.doesNotMatch(appTemplate, /v-if="\$route\.meta/, '不应在 App.vue 用 $route.meta 做 v-if 分流')
  assert.doesNotMatch(appTemplate, /currentView/, '不应再用 computed currentView 切换整个视图')
  // Vue Router 4 的 RouterView 没有 depth prop，depth 由 provide/inject 决定。
  // 写 :depth 只会把无效属性透传给页面组件，不会改变匹配层级。
  assert.doesNotMatch(appTemplate, /<router-view[^>]*:depth=/, 'RouterView 不支持 :depth，应依赖 provide/inject')
})

test('AppShell 的内容出口是嵌套 router-view', () => {
  // 只检查真实模板（去掉注释后再断言）
  const template = appShellSource.replace(/<!--[\s\S]*?-->/g, '')
  assert.match(
    template,
    /<router-view\s*\/>/,
    'AppShell 内必须有 router-view，用于渲染 depth=1 的页面组件',
  )
  assert.doesNotMatch(template, /<slot\s*\/>/, '嵌套路由方案下 AppShell 应使用 router-view 而非 slot')
  assert.doesNotMatch(template, /<router-view[^>]*:depth=/, 'RouterView 不支持 :depth，应依赖 provide/inject')
})

test('vite optimizeDeps 预构建 element-plus 的全部样式入口', () => {
  assert.match(viteConfigSource, /optimizeDeps\s*:/, '应配置 optimizeDeps')
  assert.match(viteConfigSource, /element-plus/, '应预构建 element-plus')
  assert.match(
    viteConfigSource,
    /style\/css/,
    '应预构建各组件的 style/css 入口，避免依赖分批发现触发 reload 循环',
  )
  // element-plus 的 exports 是 "./es/*" -> "./es/*.mjs"，
  // 目录形式的组件入口无法解析，写进 include 会报 "Failed to resolve dependency"
  assert.doesNotMatch(
    viteConfigSource,
    /components\/\$\{name\}`/,
    '不应 include 无法解析的目录形式组件入口',
  )
})
