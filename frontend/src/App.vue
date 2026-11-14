<template>
  <!--
    应用根组件。

    只有一个顶层 outlet：
    - 登录 / 修改密码是独立路由，直接渲染 Login / ChangePassword
    - 后台页面由 router/layout.ts 的 withAppShell(...) 包装，
      渲染 AppShell 框架 + 具体页面

    为什么不在这里用 v-if="$route.meta.public" 切两套视图：
    App 是应用根组件，其渲染函数不会因为路由变化而重新执行
    （Vue Router 只驱动 outlet 内部的组件重新渲染），
    v-if 分支在退出登录切换到 /login 时不会重新求值，
    导致「URL 与 token 都已重置，页面却仍停留在后台视图」。
    改由路由配置决定渲染结构后，切换到 /login 时
    AppShell 会被 Vue Router 正常卸载、登录页被正常挂载。
  -->
  <router-view />
</template>

<script setup lang="ts">
</script>
