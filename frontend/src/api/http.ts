import axios from 'axios'
import { ElMessageBox } from 'element-plus'

export const AI_OPERATION_TIMEOUT_MS = 330_000
const SESSION_REPLACED_CODE = 40101
let showingSessionReplacedNotice = false

function isLoginRequest(url?: string) {
  return url === '/auth/login'
}

function isLogoutRequest(url?: string) {
  return url === '/auth/logout'
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
  if (token && !isLoginRequest(config.url)) {
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
  if (error?.response?.status === 401 && !isLoginRequest(error?.config?.url)) {
    if (isLogoutRequest(error?.config?.url)) {
      // 退出接口返回 401：凭证已失效，直接清理本地状态，不做整页跳转
      // 由调用方（authStore.logout / App.logout）负责后续路由跳转
      clearLocalSession()
      return Promise.reject(error)
    }
    if (Number(error?.response?.data?.code) === SESSION_REPLACED_CODE) {
      handleSessionReplaced()
    } else {
      clearLocalSession()
      window.location.href = '/login'
    }
  }
  const message = error?.response?.data?.message || error?.message || '请求失败'
  const requestError = new Error(message) as Error & { code?: number }
  requestError.code = error?.response?.data?.code
  return Promise.reject(requestError)
})
