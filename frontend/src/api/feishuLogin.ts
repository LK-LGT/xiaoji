import { getFeishuAuthUrlApi } from './auth'

/**
 * ═══════════════════════════════════════════════════════════════
 * 飞书免登发起器（FS-2）
 * ═══════════════════════════════════════════════════════════════
 *
 * 把「发起 OAuth 授权」这段逻辑抽成独立模块，好让两个调用方共用：
 * - `Login.vue` 的「飞书登录」按钮
 * - `FeishuCallback.vue` 失败后的「重新发起飞书登录」按钮
 *
 * ⚠️ **为什么 `redirect_uri` 用当前页面的 origin 推导，而不是写死**：
 * 写死的话，开发（localhost:5180）与生产（正式域名）必须改代码才能切换。
 * 用 `window.location.origin` 推导 → 同一份构建产物在两个环境都能用，
 * 只需后端白名单里配好对应地址。
 *
 * ⚠️ **但仍受后端白名单约束** —— `FEISHU_OAUTH_REDIRECT_URIS` 里没配的地址，
 * 后端会直接 400 拒绝（这是防开放重定向的第二道防线）。
 * 所以白名单里**必须**包含开发与生产两个 origin 的回调地址。
 */

/** 回调页路径（必须与飞书后台「重定向 URL」**逐字一致**）。 */
export const FEISHU_CALLBACK_PATH = '/feishu/callback'

/**
 * ⚠️ **「登录后想回的站内路径」的存放位置 —— 这里有个坑，绕不过去。**
 *
 * 直觉做法：把 `redirect` 拼进 `redirect_uri` 的 query，
 * 让飞书回调时原样带回来。**但那行不通** ——
 * 飞书做的是 `redirect_uri` **字符串精确匹配**：
 *   - 后台配的是 `https://perf.example.com/feishu/callback`
 *   - 我们发的是 `https://perf.example.com/feishu/callback?redirect=%2Femployee`
 *   → **两个不同的字符串** → 飞书直接拒绝授权。
 *
 * 试过「把 redirect 挂在授权 URL 上」？也不行 ——
 * 飞书只会把 `code` + `state` 带回调，**不会**透传授权 URL 上的其它参数。
 *
 * 所以只能用**同源存储**带走：发起授权时存 sessionStorage，
 * 回调页读取后清理。sessionStorage 恰好合适：
 * - 同源同标签页可见（回调是**同一次跳转**，不会跨标签页）；
 * - 关掉标签页即失效（不会残留过期的跳转意图）；
 * - 刷新页面仍在（回调页刷新时还能拿到）。
 */
export const FEISHU_REDIRECT_INTENT_KEY = 'planning_feishu_redirect_intent'

/**
 * 推导本次使用的回调地址。
 *
 * @param origin 页面 origin，缺省取 `window.location.origin`。
 */
export function resolveFeishuRedirectUri(origin?: string): string {
  const base = origin || window.location.origin
  // ⚠️ 去掉末尾斜杠 —— `https://x.com/` 与 `https://x.com` 是**两个不同的字符串**，
  //   飞书做的是精确匹配，末尾多一个斜杠就会被拒（Runbook §2.5.3 第 2 条）。
  return `${base.replace(/\/+$/, '')}${FEISHU_CALLBACK_PATH}`
}

/**
 * 记下「登录后想回的站内路径」。
 *
 * ⚠️ 只接受**站内绝对路径**（以 `/` 开头、不含 `//`）——
 *   `//evil.com` 是协议相对 URL，浏览器会把它当外站地址 → 开放重定向。
 */
export function rememberFeishuRedirectIntent(redirect?: string): void {
  try {
    if (!redirect) {
      sessionStorage.removeItem(FEISHU_REDIRECT_INTENT_KEY)
      return
    }
    if (!redirect.startsWith('/') || redirect.startsWith('//')) return
    sessionStorage.setItem(FEISHU_REDIRECT_INTENT_KEY, redirect)
  }
  catch {
    // 存储不可用时静默失败 —— 拿不到跳转意图只是退回默认工作台，不该拦住登录。
  }
}

/** 读取并清除「登录后想回的站内路径」。 */
export function consumeFeishuRedirectIntent(): string {
  try {
    const value = sessionStorage.getItem(FEISHU_REDIRECT_INTENT_KEY) || ''
    sessionStorage.removeItem(FEISHU_REDIRECT_INTENT_KEY)
    if (!value.startsWith('/') || value.startsWith('//')) return ''
    return value
  }
  catch {
    return ''
  }
}

/**
 * 发起飞书免登：取授权 URL → 整页跳转到飞书。
 *
 * ⚠️ **必须是整页跳转（`location.href`），不能用 `router.push`**：
 *   这是三方跳转 —— 浏览器要离开本站去飞书，再由飞书跳回来。
 *   SPA 路由跳转无法跨域跳到飞书。
 *
 * @param redirect 登录成功后想回到的站内路径（如 `/employee/month-plans`）。
 */
export async function startFeishuLogin(redirect?: string): Promise<void> {
  rememberFeishuRedirectIntent(redirect)
  const redirectUri = resolveFeishuRedirectUri()
  const { authorizeUrl } = await getFeishuAuthUrlApi(redirectUri)
  window.location.href = authorizeUrl
}
