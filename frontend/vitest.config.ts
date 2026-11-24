import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

/**
 * ⚠️ **本文件只管「组件测试」这一条腿**（CI-5 阶段 1）。
 *
 * 现状与分工
 * ------------------------------------------------------------------
 * 本项目有**两套前端测试**，各有分工，**不要合并**：
 *
 * | 测试类型 | runner | 位置 | 测什么 |
 * |---|---|---|---|
 * | 纯逻辑 / 静态约定 | `node --test` | `tests/*.test.ts` | 纯函数、源码文本约定 |
 * | **组件行为** | `vitest` | `tests/component/**` | ⚠️ **真实挂载 + 交互** |
 *
 * ⚠️ **为什么不把老测试也搬过来**
 *   43 个文件、337 例全部依赖 `node:test` 的 API（`test` / `assert`）。
 *   vitest **兼容**这套 API，但搬迁会带来：
 *   - 一次性大改（337 例全要重跑验证）；
 *   - 老测试里大量是「读源码做正则断言」，对 vitest 而言**没有收益**；
 *   - CI 里多一个 runner ⇒ 多一份维护面。
 *
 *   ⇒ **老测试留在原位**，vitest 只承担它**独有**的能力：挂载 `.vue`。
 *
 * ⚠️ **为什么必须有 vitest**
 *   `node --test`（即使带 `--experimental-strip-types`）**没有 SFC 编译能力**
 *   ⇒ `.vue` 文件无法 import ⇒ 组件挂载测试根本跑不起来。
 *   ⇒ 必须走 Vite 的编译管线 ⇒ vitest 是最短路径。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // ⚠️ **只跑组件测试目录** —— 避免与 `node --test` 的 tests/*.test.ts 重复收集。
    include: ['tests/component/**/*.test.ts'],
    // ⚠️ jsdom —— 组件挂载需要 DOM 环境。
    environment: 'jsdom',
    globals: true,
    // ⚠️ 单进程跑：jsdom + Element Plus 的全局样式注入在多 worker 下
    //    偶发互相污染（一次跑只有一个文件，收益也不大）。
    //
    // ⚠️ **vitest 5 里 ``poolOptions`` 已废弃** —— 顶层选项才是新写法。
    //    （旧写法会打 DEPRECATED 警告。）
    pool: 'forks',
    maxWorkers: 1,
    minWorkers: 1,
    setupFiles: ['tests/component/setup.ts'],
  },
})
