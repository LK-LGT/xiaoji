/**
 * 组件测试的全局准备（vitest `setupFiles`）。
 *
 * ⚠️ **为什么需要这个文件**
 * ------------------------------------------------------------------
 * 本项目的 `.vue` 组件大量使用 Element Plus 的**全局注册**组件
 * （`<el-table>` / `<el-form>` / `<el-dialog>` …）。
 *
 * ⚠️ **关键区别：生产用「按需自动导入」，测试用「全量注册」**
 *   - 生产（``vite.config.ts``）：``unplugin-vue-components`` 的
 *     ``ElementPlusResolver`` 在**编译期**把用到的 ``<el-xxx>``
 *     改写成显式 import ⇒ 产物小、构建快。
 *   - 测试：vitest **不走** ``unplugin-vue-components``
 *     （本配置只挂了 ``@vitejs/plugin-vue``）⇒
 *     ``<el-table>`` 会变成**未知自定义元素** ⇒ 渲染出空壳。
 *
 * ⇒ 组件测试里必须**手动全量注册** Element Plus。
 *
 * ⚠️ **为什么不全量注册生产**
 *   全量注册会让产物膨胀（Element Plus 完整 CSS + 全部组件），
 *   与生产的按需导入策略冲突。
 *   ⇒ **只在测试环境全量注册**。
 */
import { config } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { vi } from 'vitest'

// ⚠️ Element Plus 的 CSS 在 jsdom 里没有意义（jsdom 不做布局），
//    但**部分组件挂载时会读 CSS 变量** ⇒ 断言可能拿到 undefined。
//    ⇒ 这里只注册组件，不引 CSS —— 若某个断言需要样式，
//      应改为断言**类名/属性**（那才是稳定契约），而不是量测像素。
config.global.plugins = [ElementPlus, createPinia()]

// ⚠️ **Element Plus 依赖 ResizeObserver**，jsdom **不提供**。
//    不 stub 的话，任何含 ``el-table`` / ``el-input`` 的组件一挂载就抛
//    "ResizeObserver is not defined"。
//    ⇒ 这里给一个**空实现**的桩 —— 尺寸相关断言不适用（见上）。
if (!('ResizeObserver' in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

// ⚠️ **matchMedia** —— Element Plus 的响应式断点会用到。
if (!('matchMedia' in globalThis)) {
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// ⚠️ **axios 不应在组件测试里发真实请求**。
//    组件挂载时若触发数据加载，没有 stub 就会真的去连后端
//    ⇒ 测试挂起 / 超时 / 报网络错误。
//    ⇒ 默认全部 stub 成「永不 resolve 的空实现」，
//    需要断言请求内容的用例应自己 ``vi.mocked`` 覆盖。
//
// ⚠️ **mock 的形状必须能撑住 ``src/api/http.ts`` 的模块级代码** ——
//    那里在**导入期**就执行了：
//
//        export const http = axios.create({ baseURL: '/api', timeout: 15000 })
//        http.interceptors.request.use(...)      ← 这里
//        http.interceptors.response.use(...)     ← 这里
//
//    ⇒ ``axios.create()`` 的返回值**必须有完整的 interceptors**，
//      否则模块导入就抛 "Cannot read properties of undefined"。
//    ⇒ 之前只 mock 了 ``axios`` 顶层对象的 interceptors，漏了 create() 的返回值。
const mockInterceptors = () => ({
  request: { use: vi.fn(), eject: vi.fn() },
  response: { use: vi.fn(), eject: vi.fn() },
})

const mockAxiosInstance = () => ({
  get: vi.fn(() => Promise.resolve({ data: {} })),
  post: vi.fn(() => Promise.resolve({ data: {} })),
  put: vi.fn(() => Promise.resolve({ data: {} })),
  delete: vi.fn(() => Promise.resolve({ data: {} })),
  patch: vi.fn(() => Promise.resolve({ data: {} })),
  request: vi.fn(() => Promise.resolve({ data: {} })),
  interceptors: mockInterceptors(),
  defaults: { headers: { common: {} } },
})

vi.mock('axios', () => {
  const instance = mockAxiosInstance()
  return {
    default: {
      ...instance,
      create: vi.fn(() => mockAxiosInstance()),
    },
  }
})
