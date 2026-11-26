<template>
  <section class="page-panel">
    <div class="page-header">
      <div>
        <span class="eyebrow">RETIRED FEATURE</span>
        <h1 class="page-title">{{ title }}</h1>
        <p class="page-subtitle">
          该功能已下线 —— 页面没有实际作用（数据是写死的假数据，改了也存不进去）。
        </p>
      </div>
    </div>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="为什么下线"
      style="margin-bottom: 16px"
    >
      <template #default>
        <p>
          这两个页面对应的后端接口返回的是<strong>硬编码假数据</strong>
          （<code>GET /v1/system/roles</code> 写死 3 条角色，
          <code>GET /v1/system/permissions</code> 写死 1 个节点），
          而数据库里<strong>根本没有 <code>role</code> 表</strong>。
          页面看起来能增删改，实际<strong>存不进去</strong>。
        </p>
        <p style="margin-top: 8px">
          更重要的是：现在的权限<strong>不是</strong>「给角色配权限」，
          而是按<strong>真实身份动态判定</strong> —— 角色由系统根据员工的
          部门、岗位、汇报线<strong>自动推导</strong>，不需要人工分配。
        </p>
      </template>
    </el-alert>

    <div class="section-card">
      <div class="section-header">
        <div>
          <h2>现在该去哪里</h2>
          <p>对应的真实功能都在下面。</p>
        </div>
      </div>

      <el-table :data="alternatives" stripe>
        <el-table-column prop="label" label="功能" min-width="160" />
        <el-table-column prop="desc" label="说明" min-width="280" />
        <el-table-column label="入口" width="130">
          <template #default="{ row }">
            <el-button link type="primary" @click="go(row.path)">前往</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 「角色管理 / 权限管理」已下线页。
 *
 * ⚠️ **为什么保留这个页面而不是直接删路由**
 *   ① 老链接（书签、飞书里发过的 URL）直接 404 体验差；
 *   ② 直接删路由 ⇒ 前端构建报错、面包屑残留、测试要连带改。
 *   ⇒ 保留路由 + 显示明确说明，是成本最低、体验最好的做法。
 *
 * ⚠️ **菜单入口已移除**（见 `navigation/workspaces.ts`）——
 *    用户不会主动点进来，只有老链接才会到这儿。
 */
import { useRouter } from 'vue-router'

const router = useRouter()

const title = '角色与权限'

const alternatives = [
  {
    label: '部门/项目组',
    desc: '维护组织树、负责人与汇报线 —— 角色和数据范围都由此推导',
    path: '/system/orgs',
  },
  {
    label: '部门功能板块',
    desc: '控制「哪个部门有哪些业务板块」（与角色权限是两个独立维度）',
    path: '/system/department-modules',
  },
  {
    label: '员工管理',
    desc: '维护员工状态与归属部门 —— 员工状态会直接影响可见范围',
    path: '/system/employees',
  },
]

function go(path: string) {
  router.push(path)
}
</script>
