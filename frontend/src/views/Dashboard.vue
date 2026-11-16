<template>
  <section class="page-panel">
    <div class="page-header">
      <div>
        <span class="eyebrow">SYSTEM CONTROL / 01</span>
        <h1 class="page-title">管理工作台</h1>
        <p class="page-subtitle">查看当前系统的计划执行、成果闭环和需要管理员关注的运行信号。</p>
      </div>
      <div class="toolbar">
        <span class="data-freshness">数据更新于 {{ lastUpdated }}</span>
        <el-button :loading="loading" @click="loadStats">刷新数据</el-button>
      </div>
    </div>
    <el-row :gutter="16">
      <el-col :span="6">
        <div class="metric primary">
          <span>待审日计划数</span>
          <strong>{{ stats.pendingDayPlans }}</strong>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric warning">
          <span>逾期未审数</span>
          <strong>{{ stats.overduePendingDayPlans }}</strong>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric success">
          <span>本月成果</span>
          <strong>{{ stats.currentMonthResults }}</strong>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric primary">
          <span>计划执行闭环率</span>
          <strong>{{ stats.closureRate }}</strong>
        </div>
      </el-col>
    </el-row>
    <el-row :gutter="16" class="mt16">
      <el-col :span="6">
        <div class="metric success">
          <span>计划执行率</span>
          <strong>{{ executionRateText }}</strong>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric primary">
          <span>已审批成果</span>
          <strong>{{ stats.confirmedResults ?? 0 }}</strong>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric warning">
          <span>被驳回成果</span>
          <strong>{{ stats.rejectedResults ?? 0 }}</strong>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="metric primary">
          <span>统计范围人数</span>
          <strong>{{ stats.scopedEmployeeCount ?? 0 }}</strong>
        </div>
      </el-col>
    </el-row>
    <el-alert
      class="mt16"
      title="已接入登录、JWT/Redis 会话、月计划、日计划、成果提交与审批确认接口；计划执行统计与执行率指标已接真实数据。"
      type="success"
      :closable="false"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { getPlanningStatsApi, type PlanningStats } from '@/api/planning'

const loading = ref(false)
const stats = reactive<PlanningStats>({
  pendingDayPlans: 0,
  overduePendingDayPlans: 0,
  currentMonthResults: 0,
  closureRate: '--',
})
const lastUpdatedAt = ref(new Date())
const lastUpdated = computed(() => new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
}).format(lastUpdatedAt.value))

/**
 * 计划执行率展示文案。
 *
 * ⚠️ 没有已审批成果时后端返回 `null`（**不是 0**）——
 * 0 会被理解成「完全没干活」，而「还没提交」是另一回事，所以这里显示 `--`。
 */
const executionRateText = computed(() => {
  const rate = stats.planExecutionRate
  if (rate === null || rate === undefined) return '--'
  return `${rate.toFixed(1)}%`
})

async function loadStats() {
  loading.value = true
  try {
    Object.assign(stats, await getPlanningStatsApi())
    lastUpdatedAt.value = new Date()
  } finally {
    loading.value = false
  }
}

onMounted(loadStats)
</script>
