import { createApp } from 'vue'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import App from './App.vue'
import router from './router'
import { pinia } from './stores'
import './styles.css'
import './components/results/result-ui.css'

dayjs.locale('zh-cn')

createApp(App).use(pinia).use(router).mount('#app')

