interface LoginRequestError {
  status?: unknown
  code?: unknown
  message?: unknown
  response?: { status?: unknown }
}

function asLoginRequestError(error: unknown): LoginRequestError | undefined {
  return error && typeof error === 'object' ? error as LoginRequestError : undefined
}

export function loginErrorMessage(error: unknown): string {
  const requestError = asLoginRequestError(error)
  const status = requestError?.status ?? requestError?.response?.status

  if (status === 401) return '用户名或密码错误'
  if (typeof status === 'number' && status >= 500) return '服务器错误'

  const code = typeof requestError?.code === 'string' ? requestError.code : ''
  const message = typeof requestError?.message === 'string' ? requestError.message.trim() : ''
  if (['ECONNABORTED', 'ETIMEDOUT', 'ERR_NETWORK'].includes(code)) return '服务器错误'
  if (!status || /network error|timeout|网络连接失败/i.test(message)) return '服务器错误'

  return message || '服务器错误'
}
