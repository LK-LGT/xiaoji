<template>
  <div class="dept-workload-page">
    <!--
      ⚠️ **按后端下发的角色渲染**（2026-11-25）

      ⚠️ **为什么不用前端硬编码判断**
      前端**不知道**「仓储部的分拣员该填什么」——
      那在**库里的岗位口径表**里。
      ⇒ 前端只按后端下发的 `role` 和 `metrics` 渲染。

      ⚠️ **指标为空是正常状态**
      业务还没定「各岗位填什么指标」⇒ 后端下发空列表
      ⇒ 这里显示**明确的提示**，而不是空白页。
    -->

    <el-alert
      v-if="role === 'none'"
      type="info"
      :closable="false"
      show-icon
      title="你所在的岗位暂未纳入该板块"
      description="该板块的填报范围按「部门 + 岗位」配置。你的岗位目前没有填报项，请联系 HR 配置岗位口径。"
    />

    <template v-else>
      <!-- ① 填报区 -->
      <el-card v-if="metrics.length" shadow="never">
        <template #header>
          <div class="card-header">
            <span>今日填报</span>
            <span class="role-badge">{{ roleLabel }}</span>
          </div>
        </template>

        <el-form :model="form" label-width="120px" class="metric-form">
          <el-form-item v-for="spec in metrics" :key="spec.code" :label="spec.label">
            <el-input-number
              v-model="form[spec.code]"
              :min="spec.kind === 'count' ? 0 : 0"
              :max="spec.kind === 'count' ? 10000 : 1"
              :step="spec.kind === 'count' ? 1 : 0.01"
              :precision="spec.kind === 'count' ? 0 : 2"
              :placeholder="spec.placeholder"
            />
            <span v-if="spec.kind === 'rate'" class="unit">（0~1 比率，如 0.35 表示 35%）</span>
          </el-form-item>

          <el-form-item label="备注">
            <el-input v-model="remark" type="textarea" :rows="2" placeholder="选填" />
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="submitting" @click="onSubmit">
              提交
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-alert
        v-else
        type="warning"
        :closable="false"
        show-icon
        title="填报指标待业务确认"
        description="该板块已开通，但「各岗位填什么指标」尚未确定，因此暂时没有可填项。指标确定后无需改代码，配置即可生效。"
      />

      <!-- ② 我的填报记录 -->
      <el-card shadow="never" class="history-card">
        <template #header>
          <div class="card-header">
            <span>我的填报记录</span>
            <el-button size="small" :loading="loading" @click="loadHistory">刷新</el-button>
          </div>
        </template>

        <el-table :data="history" v-loading="loading" size="small" empty-text="暂无填报记录">
          <el-table-column prop="metricDate" label="日期" width="120" />
          <el-table-column label="指标" width="160">
            <template #default="{ row }">{{ labelOf(row.metricCode) }}</template>
          </el-table-column>
          <el-table-column label="数值" width="120" align="right">
            <template #default="{ row }">{{ formatValue(row) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
          <el-table-column prop="source" label="来源" width="90" />
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  deptWorkloadApi,
  type DeptWorkloadMetricRow,
  type DeptWorkloadMetricSpec,
} from '@/api/deptWorkload'

/** 我的角色（后端下发）。 */
const role = ref<'reporter' | 'reviewer' | 'none'>('none')
/** 我能填的指标（**后端下发**，前端不定义）。 */
const metrics = ref<DeptWorkloadMetricSpec[]>([])
/** 我的填报记录。 */
const history = ref<DeptWorkloadMetricRow[]>([])

const form = reactive<Record<string, number | undefined>>({})
const remark = ref('')
const loading = ref(false)
const submitting = ref(false)

const roleLabel = computed(() =>
  role.value === 'reporter' ? '我是填报人' : '我是核对人',
)

/** 指标码 → 展示名。 */
function labelOf(code: string): string {
  return metrics.value.find((m) => m.code === code)?.label ?? code
}

/**
 * ⚠️ **量纲不同，显示方式不同**
 * - `count` —— 「8」
 * - `rate`  —— 「35.0%」（库里存 0.35）
 */
function formatValue(row: DeptWorkloadMetricRow): string {
  const kind = metrics.value.find((m) => m.code === row.metricCode)?.kind
  if (kind === 'rate') {
    return `${(row.metricValue * 100).toFixed(1)}%`
  }
  return String(row.metricValue)
}

async function loadScope() {
  const scope = await deptWorkloadApi.myScope()
  role.value = scope.role
  metrics.value = scope.metrics ?? []
}

async function loadHistory() {
  loading.value = true
  try {
    history.value = await deptWorkloadApi.myMetrics()
  } finally {
    loading.value = false
  }
}

async function onSubmit() {
  const today = new Date().toISOString().slice(0, 10)
  submitting.value = true
  try {
    for (const spec of metrics.value) {
      const value = form[spec.code]
      if (value === undefined || value === null) continue
      await deptWorkloadApi.submitMetric({
        metricCode: spec.code,
        metricDate: today,
        metricValue: value,
        remark: remark.value,
      })
    }
    ElMessage.success('已提交')
    remark.value = ''
    for (const spec of metrics.value) {
      form[spec.code] = undefined
    }
    await loadHistory()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await loadScope()
  await loadHistory()
})
</script>

<style scoped>
.dept-workload-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.role-badge {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.metric-form {
  max-width: 520px;
}

.unit {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
