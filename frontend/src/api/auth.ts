import { http } from './http'

export interface LoginResp {
  accessToken: string
  refreshToken: string
  expiresIn: number
  /** 员工 ID（飞书 user_id，string）。 */
  userId: string
  username: string
  realName: string
  forceChangePassword: boolean
  roles: string[]
  permissions: string[]
}

export interface AuthUser {
  /** 员工 ID（飞书 user_id，string）。 */
  userId: string
  username: string
  realName: string
  mobile: string
  /** 部门 ID（飞书 department_id，string）。 */
  deptId?: string
  departmentName?: string
  /** 项目组 ID（飞书 department_id，string）。 */
  groupId?: string
  status?: number
  statusSource?: string
  lastLoginAt?: string
  forceChangePassword: boolean
  roles: string[]
  permissions: string[]
}

export function loginApi(username: string, password: string) {
  return http.post<unknown, LoginResp>('/auth/login', { username, password })
}

/**
 * ═══════════════════════════════════════════════════════════════
 * 飞书免登（FS-2）
 * ═══════════════════════════════════════════════════════════════
 *
 * ⚠️ **后端契约**（`backend/app/routes/feishu_auth.py`）：
 * - `GET  /v1/feishu/auth/url?redirect_uri=...` → `{ authorizeUrl, state, redirectUri }`
 * - `POST /v1/feishu/auth/exchange` → **与 `/v1/auth/login` 完全同构**的 `LoginResp`
 *
 * ⚠️ **为什么 `exchange` 复用 `LoginResp` 而不是新造一个类型**：
 * 后端 `/exchange` 刻意复用了 `auth._generate_token` + `_resolve_permissions`，
 * 响应结构与 `/auth/login` **逐字段一致** → 前端可以用**同一套代码**
 * 处理两种登录方式（存 token → loadMe → 跳工作台）。
 * 若这里新造类型，两边字段一旦漂移就会**静默错位**（少一个字段不报错，
 * 但权限判断会失效）。
 */

/** 授权 URL 响应。 */
export interface FeishuAuthUrlResp {
  /** 飞书授权页完整 URL —— 前端直接 `location.href = authorizeUrl` 即可。 */
  authorizeUrl: string
  /** 防 CSRF 的 state —— **回调时必须原样带回**，后端一次性校验。 */
  state: string
  /** 实际使用的回调地址（回显，便于排障）。 */
  redirectUri: string
}

/**
 * 取飞书授权 URL。
 *
 * @param redirectUri 授权成功后的回调地址，**必须**在后端白名单
 *   （`FEISHU_OAUTH_REDIRECT_URIS`）内，否则后端返回 400。
 */
export function getFeishuAuthUrlApi(redirectUri: string) {
  return http.get<unknown, FeishuAuthUrlResp>('/feishu/auth/url', {
    params: { redirect_uri: redirectUri },
  })
}

/**
 * 用飞书授权码换本系统 token。
 *
 * ⚠️ **飞书的 code 是一次性的、且只能换一次** → 失败后**不能重试**同一个 code，
 *   必须让用户重新点「飞书登录」拿新 code。回调页据此处理。
 *
 * ⚠️ 响应里**不含**飞书 token —— 后端用完即弃，前端只拿本系统 HMAC token。
 */
export function exchangeFeishuCodeApi(code: string, state: string) {
  return http.post<unknown, LoginResp>('/feishu/auth/exchange', { code, state })
}

export function getMeApi() {
  return http.get<unknown, AuthUser>('/auth/me')
}

export function logoutApi() {
  return http.post('/auth/logout')
}

export function changePasswordApi(oldPassword: string, newPassword: string) {
  return http.post('/auth/change-password', { oldPassword, newPassword })
}
