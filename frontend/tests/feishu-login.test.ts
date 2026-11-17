import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const authApi = readFileSync(
  fileURLToPath(new URL('../src/api/auth.ts', import.meta.url)),
  'utf8',
)
const feishuLoginApi = readFileSync(
  fileURLToPath(new URL('../src/api/feishuLogin.ts', import.meta.url)),
  'utf8',
)
const httpApi = readFileSync(
  fileURLToPath(new URL('../src/api/http.ts', import.meta.url)),
  'utf8',
)
const loginView = readFileSync(
  fileURLToPath(new URL('../src/views/Login.vue', import.meta.url)),
  'utf8',
)
const callbackView = readFileSync(
  fileURLToPath(new URL('../src/views/FeishuCallback.vue', import.meta.url)),
  'utf8',
)
const routerSource = readFileSync(
  fileURLToPath(new URL('../src/router/index.ts', import.meta.url)),
  'utf8',
)

// ═══════════════════════════════════════════════════════════════════
// FS-2：飞书免登 —— 前端 API 封装
// ═══════════════════════════════════════════════════════════════════

test('前端封装飞书授权 URL 与 code 换取两个接口', () => {
  assert.match(authApi, /getFeishuAuthUrlApi/)
  assert.match(authApi, /\/feishu\/auth\/url/)
  assert.match(authApi, /exchangeFeishuCodeApi/)
  assert.match(authApi, /\/feishu\/auth\/exchange/)
})

test('code 换取接口复用 LoginResp 类型（与密码登录同构）', () => {
  // ⚠️ 核心断言：必须复用 LoginResp。
  // 新造一个类型的话，两边字段一旦漂移就会「静默错位」——
  // 少一个字段不报错，但权限判断会失效（用户进了系统却什么都看不到）。
  assert.match(authApi, /exchangeFeishuCodeApi[\s\S]*?http\.post<unknown, LoginResp>/)
})

test('授权 URL 请求带上 redirect_uri 参数', () => {
  assert.match(authApi, /getFeishuAuthUrlApi[\s\S]*?params:\s*\{\s*redirect_uri:/)
})

// ═══════════════════════════════════════════════════════════════════
// FS-2：redirect_uri 的「逐字一致」约束
// ═══════════════════════════════════════════════════════════════════

test('回调地址由当前 origin 推导，不写死域名', () => {
  // ⚠️ 写死的话，开发与生产必须改代码才能切换。
  assert.match(feishuLoginApi, /window\.location\.origin/)
  assert.match(feishuLoginApi, /FEISHU_CALLBACK_PATH\s*=\s*'\/feishu\/callback'/)
})

test('回调地址去掉末尾斜杠（飞书做字符串精确匹配）', () => {
  // ⚠️ `https://x.com/` 与 `https://x.com` 是两个不同字符串，
  // 末尾多一个斜杠就会被飞书拒绝授权（Runbook §2.5.3 第 2 条）。
  //
  // ⚠️ 用 RegExp 构造器而非字面量 —— 这个模式里含未转义的 `/`，
  //   写成正则字面量会被 `/` 提前终止（语法错误）。
  assert.match(
    feishuLoginApi,
    new RegExp(String.raw`replace\(/\\/\+\$/, ''\)`),
    '必须去掉 origin 末尾的斜杠 —— https://x.com 与 https://x.com/ 是两个不同的字符串',
  )
})

test('跳转意图用 sessionStorage 传递，不塞进 redirect_uri', () => {
  // ⚠️ 这是本轮最关键的实现约束：
  //   redirect_uri 必须与飞书后台「重定向 URL」**逐字一致**，
  //   加 query 参数会被飞书直接拒绝授权。
  //   而飞书也不会透传授权 URL 上的其它参数 → 只能走同源存储。
  assert.match(feishuLoginApi, /sessionStorage\.setItem\(FEISHU_REDIRECT_INTENT_KEY/)
  assert.match(feishuLoginApi, /sessionStorage\.getItem\(FEISHU_REDIRECT_INTENT_KEY\)/)

  // ⚠️ 只查**代码**不查注释 —— 注释里写着「加 query 会被飞书拒绝」，
  //   全文匹配会把这段说明当成违规。
  const codeOnly = feishuLoginApi
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/.*$/gm, '')

  // redirectUri 一旦被拼接 query / 附加参数，就与飞书后台的白名单不匹配 → 授权被拒。
  assert.doesNotMatch(
    codeOnly,
    /redirectUri\s*[+`]/,
    'redirectUri 不得被拼接任何附加内容 —— 必须与飞书后台「重定向 URL」逐字一致',
  )
  assert.doesNotMatch(
    codeOnly,
    /redirectUri[\s\S]{0,120}?[?&](redirect|state)=/,
    'redirectUri 不得携带 query 参数 —— 飞书做字符串精确匹配，会直接拒绝授权',
  )
})

test('跳转意图只接受站内绝对路径（防开放重定向）', () => {
  // `//evil.com` 是协议相对 URL，浏览器会当外站地址处理。
  assert.match(feishuLoginApi, /redirect\.startsWith\('\/'\)/)
  assert.match(feishuLoginApi, /redirect\.startsWith\('\/\/'\)/)
})

test('发起授权用整页跳转而非 SPA 路由', () => {
  // ⚠️ 三方跳转必须让浏览器离开本站 → location.href，不能 router.push。
  assert.match(feishuLoginApi, /window\.location\.href\s*=\s*authorizeUrl/)
  // ⚠️ 只查**代码**不查注释 —— 注释里写着「不能用 router.push」，
  //   直接全文匹配会把这条说明当成违规。
  const codeOnly = feishuLoginApi
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/.*$/gm, '')
  assert.doesNotMatch(codeOnly, /router\.push/)
})

// ═══════════════════════════════════════════════════════════════════
// FS-2：全局 401 拦截器不得劫持免登失败
// ═══════════════════════════════════════════════════════════════════

test('免登 code 换取接口被排除在 401 全局跳转之外', () => {
  // ⚠️ 用户此刻正站在回调页上。若被全局拦截器跳走，
  //   「授权失败」提示根本没机会展示 → 用户只看到「闪回登录页」，一脸茫然。
  assert.match(httpApi, /isLoginLikeRequest/)
  assert.match(httpApi, /\/feishu\/auth\/exchange/)
  assert.match(
    httpApi,
    /status === 401 && !isLoginLikeRequest\(/,
    '401 拦截必须放行登录类接口',
  )
})

test('旧的 isLoginRequest 已被 isLoginLikeRequest 取代（不留死代码）', () => {
  assert.doesNotMatch(httpApi, /function isLoginRequest\(/)
})

// ═══════════════════════════════════════════════════════════════════
// FS-2：登录页的「飞书登录」入口
// ═══════════════════════════════════════════════════════════════════

test('登录页有飞书登录入口且调用发起器', () => {
  assert.match(loginView, /飞书登录/)
  assert.match(loginView, /startFeishuLogin/)
  assert.match(loginView, /@click="feishuLogin"/)
})

test('密码登录保留且飞书入口是并列的另一条路（不替代）', () => {
  // ⚠️ Runbook §3.2 约束 1：飞书故障时全员无法登录是灾难。
  //   密码表单必须原样保留，不能被飞书入口取代。
  assert.match(loginView, /进入工作空间/)
  assert.match(loginView, /loginApi|authStore\.login/)
  assert.match(loginView, /type="password"/)
  // 飞书按钮在密码表单**之后**（并列而非前置）
  const passwordFormIndex = loginView.indexOf('type="password"')
  const feishuButtonIndex = loginView.indexOf('飞书登录')
  assert.ok(
    passwordFormIndex > -1 && feishuButtonIndex > passwordFormIndex,
    '飞书入口必须在密码表单之后，不能取代密码登录',
  )
})

test('登录页飞书按钮有独立的 loading 态', () => {
  assert.match(loginView, /const feishuLoading = ref\(false\)/)
  assert.match(loginView, /:loading="feishuLoading"/)
})

// ═══════════════════════════════════════════════════════════════════
// FS-2：OAuth 回调页
// ═══════════════════════════════════════════════════════════════════

test('回调页已注册路由且标记为 public', () => {
  // ⚠️ 必须 public: true —— 用户此刻还没有本系统 token
  //   （token 是回调页里用 code 换出来的），
  //   走全局守卫会被拦回 /login → 「回调 → 踢去登录 → 回调永不发生」死循环。
  assert.match(routerSource, /path: '\/feishu\/callback'/)
  assert.match(
    routerSource,
    /path: '\/feishu\/callback'[\s\S]{0,120}?meta:\s*\{\s*public:\s*true\s*\}/,
    '回调路由必须 public: true，否则会被全局守卫拦回登录页形成死循环',
  )
})

test('全局路由守卫对 public 路由直接放行', () => {
  // ⚠️ 光把路由标成 public 还不够 —— 守卫必须**真的**先判 public 再放行。
  //   顺序反了（先查 token 再判 public）→ 回调页照样被踢回登录页。
  assert.match(
    routerSource,
    /beforeEach[\s\S]{0,200}?if\s*\(\s*to\.meta\.public\s*\)\s*return\s+true/,
    '全局守卫必须先判断 meta.public 并直接放行',
  )
})

test('回调页处理飞书返回的 error 参数（用户拒绝授权）', () => {
  assert.match(callbackView, /readQuery\('error'\)/)
  assert.match(callbackView, /error_description/)
})

test('回调页校验 code 与 state 完整性', () => {
  assert.match(callbackView, /readQuery\('code'\)/)
  assert.match(callbackView, /readQuery\('state'\)/)
  assert.match(callbackView, /回调参数不完整/)
})

test('回调页用 code 换 token 并写入本系统凭证', () => {
  assert.match(callbackView, /exchangeFeishuCodeApi\(code, state\)/)
  assert.match(callbackView, /localStorage\.setItem\('planning_access_token'/)
  assert.match(callbackView, /localStorage\.setItem\('planning_refresh_token'/)
  assert.match(callbackView, /authStore\.loadMe\(\)/)
})

test('回调页不存储任何飞书 token', () => {
  // ⚠️ 前端只存**本系统** HMAC token，飞书 token 绝不能落到浏览器存储。
  //   扫描范围覆盖登录页 + 回调页 + 两个 API 模块 ——
  //   只查回调页的话，把 token 存到登录页就漏网了。
  const frontendSources = [loginView, callbackView, feishuLoginApi, authApi].join('\n')
  assert.doesNotMatch(frontendSources, /user_access_token|userAccessToken/)
  assert.doesNotMatch(frontendSources, /feishu_access_token|feishuAccessToken/)
  assert.doesNotMatch(callbackView, /localStorage\.setItem\([^)]*feishu/i)
})

test('回调页幂等：同一个 code 只处理一次', () => {
  // ⚠️ Runbook §3.2 约束 4：OAuth 回调可能重复触发（用户刷新），
  //   而 code 用一次就废 → 重复提交只会拿到「code 已被使用」，
  //   把本已登录的用户误导到错误页。
  assert.match(callbackView, /HANDLED_KEY/)
  assert.match(callbackView, /sessionStorage\.getItem\(HANDLED_KEY\)/)
  assert.match(callbackView, /sessionStorage\.setItem\(HANDLED_KEY, code\)/)
})

test('回调页失败时提供密码登录降级出口', () => {
  assert.match(callbackView, /改用账号密码登录/)
  assert.match(callbackView, /goPasswordLogin/)
  assert.match(callbackView, /router\.replace\('\/login'\)/)
})

test('回调页失败时提供重新发起授权的出口', () => {
  // ⚠️ 重新发起必须走完整流程（取新 URL → 跳飞书），
  //   不能复用旧 code —— 旧 code 已被消费，state 也已被后端作废。
  assert.match(callbackView, /重新发起飞书登录/)
  assert.match(callbackView, /await startFeishuLogin\(\)/)
})

test('回调页回跳目标做权限校验且排除回调页自身', () => {
  // ⚠️ 排除 '/feishu/callback' —— 否则会「回调 → 跳回调 → 再回调」死循环。
  assert.match(callbackView, /authorizedRedirect/)
  assert.match(callbackView, /hasPermission\(permissions, requiredPermission\)/)
  assert.match(callbackView, /'\/feishu\/callback'/)
})

test('回调页回跳意图来自 sessionStorage 而非 URL 参数', () => {
  assert.match(callbackView, /consumeFeishuRedirectIntent\(\)/)
})
