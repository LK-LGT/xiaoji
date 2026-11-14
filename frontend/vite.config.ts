import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'

/**
 * Element Plus 组件清单。
 *
 * unplugin-vue-components 的 ElementPlusResolver 会为每个用到的 <el-xxx> 生成
 * `import ... from 'element-plus/es/components/<name>/style/css'` 这类副作用导入。
 * 如果不提前用 optimizeDeps.include 预打包，Vite 的依赖预构建会在「扫描到新依赖」
 * 时分批发现它们并触发 "optimized dependencies changed. reloading"，
 * 导致页面在开发过程中反复整页刷新（模块处于半初始化状态，路由组件偶发渲染为空）。
 *
 * 这里把实际使用到的组件 style/css 入口全部显式列出，一次性完成预构建。
 *
 * 注意两点：
 * 1. 不能 include `element-plus/es/components/<name>` 形式的组件入口。
 *    element-plus 的 exports 字段是 `"./es/*": { "import": "./es/*.mjs" }`，
 *    只支持 .mjs 后缀的扁平路径，目录形式的子路径无法被解析（Vite 会报
 *    "Failed to resolve dependency"）。组件本体由 `element-plus/es` 整体引入即可。
 * 2. 子组件（table-column / form-item / option 等）没有独立入口，
 *    但它们的 style/css 路径是可解析的，因此 style/css 全部列出。
 */
const ELEMENT_PLUS_STYLE_COMPONENTS = [
  'alert',
  'base',
  'button',
  'button-group',
  'calendar',
  'card',
  'checkbox',
  'checkbox-button',
  'checkbox-group',
  'col',
  'collapse',
  'collapse-item',
  'config-provider',
  'date-picker',
  'descriptions',
  'descriptions-item',
  'dialog',
  'drawer',
  'dropdown',
  'dropdown-item',
  'dropdown-menu',
  'empty',
  'form',
  'form-item',
  'icon',
  'input',
  'input-number',
  'loading',
  'option',
  'option-group',
  'pagination',
  'popover',
  'progress',
  'radio-button',
  'radio-group',
  'result',
  'row',
  'segmented',
  'select',
  'skeleton',
  'slider',
  'switch',
  'table',
  'table-column',
  'tab-pane',
  'tabs',
  'tag',
  'timeline',
  'timeline-item',
  'tooltip',
  'tree',
  'tree-select',
  'upload',
] as const

const elementPlusOptimizeDeps = [
  'element-plus',
  'element-plus/es',
  'element-plus/es/locale/lang/zh-cn',
  ...ELEMENT_PLUS_STYLE_COMPONENTS.map((name) => `element-plus/es/components/${name}/style/css`),
]

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [
      vue(),
      Components({
        resolvers: [ElementPlusResolver()],
        directives: true,
        dts: false,
      }),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    optimizeDeps: {
      // 一次性预构建，避免依赖分批发现导致的反复 reload
      include: elementPlusOptimizeDeps,
    },
    server: {
      port: Number(env.VITE_PORT || 5180),
      proxy: {
        '/api': {
          target: env.VITE_API_TARGET || 'http://localhost:8000',
          changeOrigin: true,
          // 映射规则：/api/xxx → /v1/xxx
          rewrite: (path) => path.replace(/^\/api/, '/v1'),
        },
      },
    },
  }
})
