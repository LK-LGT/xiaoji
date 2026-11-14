import { createApp } from 'vue'
import dayjs from 'dayjs'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'dayjs/locale/zh-cn'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import App from './App.vue'
import router from './router'
import { pinia } from './stores'
import './styles.css'
import './components/results/result-ui.css'

dayjs.locale('zh-cn')

// 全局配置 Element Plus 中文 locale。
// 之前在 AppShell 里用 <el-config-provider> 局部配置，
// 其默认 slot 会缓存分支 vnode，与路由切换时的组件卸载冲突，
// 导致退出登录后 DOM patch 抛错、后台框架残留在登录页上。
createApp(App)
  .use(pinia)
  .use(router)
  .use(ElementPlus, { locale: zhCn })
  .mount('#app')
