import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import App from './App.vue'
import router from './router'

// Element Plus 走**全量引入**：少一层构建配置，内部系统不为体积做取舍。
// （真要做按需引入，需要再加 unplugin-auto-import + unplugin-vue-components 两个插件。）
createApp(App)
  .use(createPinia())
  .use(router)
  .use(ElementPlus, { locale: zhCn })
  .mount('#app')
