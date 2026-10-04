import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api 转发到后端，前端就变成"同源调用"——
// 这样后端完全不用配 CORS 白名单，而且和将来的生产部署（同域反代）形态一致，
// 本地怎么调、线上就怎么跑，不会出现"本地好用、部署后跨域失败"。
export default defineConfig({
  plugins: [vue()],
  server: {
    // 显式绑 IPv4。默认的 host='localhost' 在 Windows 上会解析到 IPv6 的 ::1，
    // 于是服务只监听 [::1]:5173 —— 用 127.0.0.1 访问会连不上，curl / 反向代理
    // 之类的工具会一脸"连接被拒绝"。绑死 127.0.0.1 就没有这个歧义。
    // （要让局域网内其它机器访问，把它改成 '0.0.0.0'。）
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // 上传大文件（后端默认上限 20MB）时不要让代理提前断开
        timeout: 120000,
      },
    },
  },
  build: {
    // 不生成 sourcemap：内部系统不需要，避免把源码结构带进产物
    sourcemap: false,
  },
})
