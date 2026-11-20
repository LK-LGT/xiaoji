/**
 * P0-2 分布质检（Distribution QC）面板。
 *
 * 用途：主管 / 部门负责人在**定稿前**看到本组分数的分布统计，
 * 以及系统给出的"核查线索"（松紧度、异常分）。
 *
 * ⚠️⚠️ 本组件是**只读观察面板**，三条硬约束：
 * 1. **不提供任何改分入口** —— 组件里没有按钮能写回分数或等级；
 * 2. **不显示"通过/不通过"结论** —— 只呈现统计量与核查线索，
 *    判断权始终在人手里；
 * 3. **后端 limitations 原样透出** —— 小样本保护、不可横向比较等
 *    前提必须让用户看见，否则统计量会被误读为结论。
 */
<template>
  <section class="distribution-qc-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">分布质检</span>
        <h3 class="panel-title">分数分布与核查线索</h3>
        <p class="panel-subtitle">
          {{ summaryText }}
        </p>
      </div>
      <div class="head-actions">
        <el-select
          v-model="period"
          placeholder="选择周期"
          class="period-select"
          @change="run(load)"
        >
          <el-option v-for="p in periodOptions" :key="p" :label="p" :value="p" />
        </el-select>
        <el-button size="small" :loading="loading" @click="run(load)">
          重新检测
        </el-button>
      </div>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-skeleton v-if="loading && !report" :rows="4" animated />

    <template v-else-if="report">
      <!-- 免责与前提：始终在 DOM，不折叠 -->
      <el-alert type="info" :closable="false" show-icon class="limitation-alert">
        <template #title>只提示，不改分</template>
        <ul class="limitation-list">
          <li v-for="(text, i) in report.limitations" :key="i">{{ text }}</li>
        </ul>
      </el-alert>

      <!-- 分组统计 -->
      <div v-if="report.groups.length" class="group-block">
        <h4 class="section-title">分组统计</h4>
        <el-table :data="report.groups" size="small">
          <el-table-column label="部门" min-width="130">
            <template #default="{ row }">{{ row.department_id }}</template>
          </el-table-column>
          <el-table-column label="岗位" min-width="110">
            <template #default="{ row }">{{ row.position || '—' }}</template>
          </el-table-column>
          <el-table-column label="人数" width="70">
            <template #default="{ row }">{{ row.count }}</template>
          </el-table-column>
          <el-table-column label="均值" width="90">
            <template #default="{ row }">
              <span v-if="row.mean !== null">{{ row.mean }}</span>
              <span v-else class="muted" title="样本不足，不展示均值">—</span>
            </template>
          </el-table-column>
          <el-table-column label="标准差" width="90">
            <template #default="{ row }">
              <span v-if="row.stddev !== null">{{ row.stddev }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="变异系数" width="100">
            <template #default="{ row }">
              <span v-if="row.cv !== null">{{ row.cv }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="等级分布" min-width="170">
            <template #default="{ row }">
              <div class="rating-chips">
                <el-tag
                  v-for="(info, level) in row.rating_distribution"
                  :key="level"
                  size="small"
                  effect="plain"
                >
                  {{ level }} {{ info.count }}人
                  <template v-if="info.percentage !== null">
                    （{{ Math.round(info.percentage * 100) }}%）
                  </template>
                </el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="异常分" width="90">
            <template #default="{ row }">
              <span v-if="row.outliers?.length" class="outlier-count">
                {{ row.outliers.length }} 人
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 核查线索 -->
      <div v-if="report.alerts.length" class="alert-block">
        <h4 class="section-title">
          核查线索
          <el-tag size="small" effect="plain">{{ report.alerts.length }} 项</el-tag>
        </h4>
        <p class="section-note">
          以下是系统基于统计量给出的**核查线索**，不是结论 —— 请结合实际情况判断。
        </p>
        <article v-for="(alert, i) in report.alerts" :key="i" class="qc-alert">
          <div class="alert-head">
            <el-tag :type="severityType(alert.severity)" size="small" effect="dark">
              {{ severityLabel(alert.severity) }}
            </el-tag>
            <strong>{{ alertTypeLabel(alert.type) }}</strong>
            <span class="alert-scope">
              {{ alert.department_id }} · {{ alert.position || '全岗位' }}
            </span>
          </div>
          <p class="alert-message">{{ alert.message }}</p>
        </article>
      </div>

      <el-empty
        v-if="!report.groups.length && !report.alerts.length"
        :image-size="80"
        description="本周期暂无可统计的分数分布"
      />
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  distributionQcApi,
  type DistributionReport,
} from '@/api/p0p1'

const props = withDefaults(
  defineProps<{
    /** 周期（紧凑格式，如 202611）；留空则使用父级传入或当前周期 */
    period?: string | null
    /** 限定部门（逗号分隔）；留空表示按权限看全部 */
    departmentIds?: string
    /** 是否自动加载 */
    autoLoad?: boolean
  }>(),
  { autoLoad: true },
)

const emit = defineEmits<{
  (e: 'loaded', report: DistributionReport): void
}>()

const loading = ref(false)
const error = ref('')
const report = ref<DistributionReport | null>(null)
const period = ref(props.period ?? '')

/** 周期候选：当前周期 + 前后各 3 期。 */
const periodOptions = computed(() => {
  const now = new Date()
  const list: string[] = []
  for (let offset = 0; offset <= 6; offset += 1) {
    const d = new Date(now.getFullYear(), now.getMonth() - offset, 1)
    list.push(`${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}`)
  }
  return list
})

const summaryText = computed(() => {
  if (!report.value) return '加载中…'
  const alerts = report.value.alerts.length
  const groups = report.value.groups.length
  if (!groups) return '本周期暂无可统计的分数分布'
  if (!alerts) return `${groups} 个分组未发现异常`
  return `${groups} 个分组，${alerts} 项核查线索`
})

function severityType(severity: string): string {
  const map: Record<string, string> = {
    HIGH: 'danger',
    BLOCKING: 'danger',
    MEDIUM: 'warning',
    LOW: 'info',
  }
  return map[severity] ?? 'info'
}

function severityLabel(severity: string): string {
  const map: Record<string, string> = {
    HIGH: '高',
    BLOCKING: '阻断',
    MEDIUM: '中',
    LOW: '低',
  }
  return map[severity] ?? severity
}

function alertTypeLabel(type: string): string {
  const map: Record<string, string> = {
    SMALL_SAMPLE: '样本不足',
    TOO_LENIENT: '区分度过低',
    SCORE_OUTLIER: '异常分',
    DISTRIBUTION_GAP: '分布差异',
  }
  return map[type] ?? type
}

async function run(fn: () => Promise<unknown>) {
  loading.value = true
  error.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function load() {
  const target = period.value || props.period
  if (!target) {
    error.value = '缺少考核周期'
    return
  }
  const data = await distributionQcApi.distribution(
    target,
    props.departmentIds ?? '',
  )
  report.value = data
  emit('loaded', data)
}

if (props.autoLoad) {
  void run(load)
}
</script>

<style scoped>
.distribution-qc-panel {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px;
  margin-top: 16px;
  background: var(--el-bg-color);
}
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.head-text {
  min-width: 0;
}
.eyebrow {
  font-size: 12px;
  color: var(--el-color-info);
  letter-spacing: 1px;
}
.panel-title {
  margin: 2px 0 4px;
  font-size: 16px;
}
.panel-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}
.period-select {
  width: 130px;
}
.limitation-alert {
  margin-bottom: 12px;
}
.limitation-list {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.section-title {
  margin: 16px 0 6px;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-note {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.rating-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.muted {
  color: var(--el-text-color-placeholder);
}
.outlier-count {
  color: var(--el-color-warning);
}
.qc-alert {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
}
.alert-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.alert-scope {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.alert-message {
  margin: 6px 0 0;
  font-size: 13px;
}
</style>
