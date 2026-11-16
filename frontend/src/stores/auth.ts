import { defineStore } from 'pinia'
import { changePasswordApi, getMeApi, loginApi, logoutApi, type AuthUser } from '@/api/auth'

interface AuthState {
  token: string
  refreshToken: string
  user: AuthUser | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: localStorage.getItem('planning_access_token') || '',
    refreshToken: localStorage.getItem('planning_refresh_token') || '',
    user: null,
  }),
  actions: {
    async login(username: string, password: string) {
      const data = await loginApi(username, password)
      this.token = data.accessToken
      this.refreshToken = data.refreshToken
      localStorage.setItem('planning_access_token', data.accessToken)
      localStorage.setItem('planning_refresh_token', data.refreshToken)
      await this.loadMe()
    },
    async loadMe() {
      if (!this.token) return
      this.user = await getMeApi()
    },
    async changePassword(oldPassword: string, newPassword: string) {
      await changePasswordApi(oldPassword, newPassword)
      // ⚠️ BUG-R6-17：改密接口**已经**把 `token_version` 递增过了，
      //   服务端的旧 Token 此刻已失效。再调一次 `/auth/logout` 会带着
      //   一个失效 Token 请求 —— 后端为了幂等仍会返回「登出成功」，
      //   但历史上它还会**再递增一次** `token_version`，
      //   连带把其它设备上正在用的会话一起踢下线。
      //
      //   这里只清本地凭证（强制重新登录），不再请求 logout 接口。
      this.clearLocalSession()
    },
    /** 清空本地登录态（不请求服务端）。 */
    clearLocalSession() {
      // 1. 清空内存中的登录状态
      this.token = ''
      this.refreshToken = ''
      this.user = null
      // 2. 清空本地存储中的登录凭证
      localStorage.removeItem('planning_access_token')
      localStorage.removeItem('planning_refresh_token')
      // 3. 清理会话级缓存
      sessionStorage.clear()
    },
    async logout() {
      // 无论服务端接口是否成功，本地凭证都必须在退出流程中清空
      let apiError: unknown = null
      try {
        await logoutApi()
      } catch (error) {
        apiError = error
        console.error('Logout API error:', error)
      } finally {
        this.clearLocalSession()
      }
      // 接口异常不再向上抛出，避免阻断后续的路由跳转
      if (apiError) return
    },
  },
})
