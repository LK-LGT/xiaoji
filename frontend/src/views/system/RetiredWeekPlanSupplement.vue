<template>
  <section class="page-panel">
    <div class="page-header">
      <div>
        <span class="eyebrow">RETIRED FEATURE</span>
        <h1 class="page-title">周计划补录（已下线）</h1>
        <p class="page-subtitle">该页面暂未开放 —— 后端尚未实现真实数据存储。</p>
      </div>
    </div>

    <el-alert type="warning" :closable="false" show-icon title="为什么下线" style="margin-bottom: 16px">
      <template #default>
        <p>
          原来的「周计划补录」页面可以打开，但后端列表恒为空、创建/删除只回显入参，
          <strong>数据库里什么都没写</strong>。为了避免「看起来能用、实际存不进去」，
          本页先下线。
        </p>
        <p style="margin-top: 8px">
          需要补录时，请先在周计划页面提交/撤回；如确有历史补录需求，请联系管理员登记。
        </p>
      </template>
    </el-alert>

    <div class="section-card">
      <div class="section-header">
        <div>
          <h2>现在该去哪里</h2>
          <p>周计划的真实入口如下。</p>
        </div>
      </div>
      <el-table :data="alternatives" stripe>
        <el-table-column prop="label" label="功能" min-width="160" />
        <el-table-column prop="desc" label="说明" min-width="280" />
        <el-table-column label="入口" width="130">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(row.path)">前往</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
const alternatives = [
  { label: '我的周计划', desc: '员工/主管查看、编辑、提交自己的周计划', path: '/employee/week-plans' },
  { label: '月计划审批', desc: '主管审批月计划；周计划按实际提交记录处理', path: '/leader/month-plan-approval' },
]
</script>
