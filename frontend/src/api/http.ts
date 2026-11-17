import axios from 'axios'
import { ElMessageBox } from 'element-plus'

export const AI_OPERATION_TIMEOUT_MS = 330_000
const SESSION_REPLACED_CODE = 40101
let showingSessionReplacedNotice = false

/**
 * ⚠️ FS-2：飞书免登的 code 换取接口**也是登录入口**。
 *
 * 为什么必须单独排除：
 * 通用规则是「非登录请求收到 401 → 清凭证 + 跳 `/login`」。
 * 但 `/feishu/auth/exchange` 失败时（code 过期 / state 重放 / 账号未绑定）
 * 用户正**站在回调页**上。此时若被全局拦截器跳走，用户会看到：
 *   1. 回调页上「授权失败」的提示**根本没机会展示**（整页被替换）；
 *   2. 被丢到一个**不带任何错误信息**的 `/login` —— 用户不知道发生了什么，
 *      只会以为「点了飞书登录就闪回登录页」，一脸茫然。
 *
 * 所以：登录类接口的失败**一律原样抛给调用方**（`Login.vue` / `FeishuCallback.vue`）
 * 由页面自己展示可操作的提示（如「该飞书账号未绑定，请用密码登录」）。
 */
function isLoginLikeRequest(url?: string) {
  return url === '/auth/login' || url === '/feishu/auth/exchange'
}

function isLogoutRequest(url?: string) {
  return url === '/auth/logout'
}

/**
 * ⚠️ BUG-R6-38：这些接口**不能**在 401 时全局跳登录页。
 *
 * 场景：用户在「首次登录，请修改密码」页输入旧密码时输错，
 * 后端返回 401（"旧密码不正确"）。
 * 过去的拦截器逻辑是「任何非登录请求 401 → 清凭证 + `location.href='/login'`」，
 * 于是：
 *   1. 用户**看不到**"旧密码不正确"的提示（错误被跳转吃掉了）；
 *   2. 刚输入的新密码**全部丢失**，得从头再输一遍；
 *   3. 更糟的是改密接口的 401 语义是「口令不对」，
 *      而不是「登录态失效」—— 把它当成会话过期处理本身就是**语义错误**。
 *
 * 现在：改密 / 登出之外的「凭证型 401」才跳登录；
 * 改密接口的 401 原样抛给调用方（页面自己展示错误）。
 */
function isPasswordChangeRequest(url?: string) {
  return url === '/auth/change-password'
}

function clearLocalSession() {
  localStorage.removeItem('planning_access_token')
  localStorage.removeItem('planning_refresh_token')
}

function handleSessionReplaced() {
  clearLocalSession()
  if (showingSessionReplacedNotice) return
  showingSessionReplacedNotice = true
  const redirectToLogin = () => {
    window.location.href = '/login'
  }
  void ElMessageBox.alert(
    '账号已在其他设备登录，当前登录已失效，请重新登录。',
    '账号已下线',
    {
      type: 'warning',
      confirmButtonText: '重新登录',
      closeOnClickModal: false,
      closeOnPressEscape: false,
      showClose: false,
    },
  ).then(redirectToLogin, redirectToLogin)
}

export const http = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('planning_access_token')
  if (token && !isLoginLikeRequest(config.url)) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use((response) => {
  const body = response.data
  if (body && typeof body.code !== 'undefined') {
    if (body.code !== 0) {
      return Promise.reject(new Error(body.message || '请求失败'))
    }
    return body.data
  }
  return body
}, (error) => {
  if (axios.isCancel(error) || error?.code === 'ERR_CANCELED') {
    return Promise.reject(error)
  }
  if (error?.response?.status === 401 && !isLoginLikeRequest(error?.config?.url)) {
    if (isLogoutRequest(error?.config?.url)) {
      // 退出接口返回 401：凭证已失效，直接清理本地状态，不做整页跳转
      // 由调用方（authStore.logout / App.logout）负责后续路由跳转
      clearLocalSession()
      return Promise.reject(error)
    }
    if (isPasswordChangeRequest(error?.config?.url)) {
      // ⚠️ BUG-R6-38：改密接口的 401 = "旧密码不正确"，
      // 不是"登录态失效"。直接抛给页面展示，**不要**跳转登录页，
      // 否则用户输入的旧/新密码会被整页刷新清空。
      //
      // ⚠️ 后端 FastAPI 的 HTTPException 体是 ``{"detail": "..."}``，
      //   而本项目**自定义**的业务错误体是 ``{"code": ..., "message": ...}``。
      //   只读 message 会拿到 undefined → 前端只能显示
      //   "Request failed with status code 401" 这种天书。
      //   这里两个字段都兜住。
      const data = error?.response?.data
      const message =
        data?.message || data?.detail || '旧密码不正确，请重新输入'
      const requestError = new Error(
        typeof message === 'string' ? message : '旧密码不正确，请重新输入',
      ) as Error & { code?: number }
      requestError.code = data?.code
      return Promise.reject(requestError)
    }
    if (Number(error?.response?.data?.code) === SESSION_REPLACED_CODE) {
      handleSessionReplaced()
    } else {
      clearLocalSession()
      window.location.href = '/login'
    }
  }
  // ⚠️ 同上：FastAPI HTTPException 的错误体是 ``{"detail": "..."}``，
  //   本项目自定义错误体是 ``{"code": ..., "message": ...}``。
  //   两个都兜住，否则用户看到的是 "Request failed with status code XXX"。
  //
  // ⚠️⚠️ **detail 可能是对象**（2026-11-17 新增）：
  //   免登的 ``identity_not_bound`` 需要区分「已离职 / 已停用 / 未同步 / 数据冲突」，
  //   所以后端返回 ``{"detail": {"message": "...", "reason": "exited"}}``。
  //   旧代码只认字符串 —— 对象会退化成 "请求失败"，把有用信息全丢了。
  const data = error?.response?.data
  const detail = data?.detail
  const rawMessage =
    data?.message ||
    (typeof detail === 'string' ? detail : detail?.message) ||
    error?.message ||
    '请求失败'
  const message = typeof rawMessage === 'string' ? rawMessage : '请求失败'
  const requestError = new Error(message) as Error & {
    code?: number
    reason?: string
  }
  requestError.code = data?.code
  // ⚠️ 透传细粒度诊断码（exited / disabled / not_synced / ambiguous）——
  //   让页面能给出**针对性**提示，而不是显示一句通用文案。
  requestError.reason =
    (typeof detail === 'object' && detail !== null ? detail.reason : undefined) ??
    data?.reason
  return Promise.reject(requestError)
})
