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

export function getMeApi() {
  return http.get<unknown, AuthUser>('/auth/me')
}

export function logoutApi() {
  return http.post('/auth/logout')
}

export function changePasswordApi(oldPassword: string, newPassword: string) {
  return http.post('/auth/change-password', { oldPassword, newPassword })
}
