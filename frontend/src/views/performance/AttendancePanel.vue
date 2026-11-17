<template>
  <div class="attendance-panel">
    <!-- 头部：周期 + 数据截至时间 -->
    <div class="panel-header">
      <div class="panel-title-row">
        <h3 class="panel-title">考勤参考</h3>
        <el-tag v-if="periodDisplay" size="small" type="info" effect="plain">
          {{ periodDisplay }}
        </el-tag>
      </div>
      <div class="panel-meta">
        <span v-if="asOf" class="as-of">数据截至 {{ formatAsOf(asOf) }}</span>
        <span v-else class="as-of muted">数据截至时间未知</span>
      </div>
    </div>

    <!-- 六类异常：全部展示，无数据时显示 0 +【暂无数据】 -->
    <div class="anomaly-grid">
      <div
        v-for="item in items"
        :key="item.type"
        class="anomaly-card"
        :class="{ 'is-empty': item.hasNoData }"
      >
        <div class="anomaly-label">{{ item.label }}</div>
        <div class="anomaly-value">
          <span class="number" :class="{ muted: item.count === 0 }">
            {{ item.count }}
          </span>
          <span v-if="item.hasNoData" class="no-data-tag">【暂无数据】</span>
          <span v-else-if="item.count === 0" class="no-data-tag soft">无异常</span>
        </div>
        <div v-if="item.days !== null" class="anomaly-days">
          涉及 {{ item.days }} 天
        </div>
      </div>
    </div>

    <!-- 加载 / 错误 / 空态 -->
    <div v-if="loading" class="panel-state">
      <el-skeleton :rows="2" animated />
    </div>
    <div v-else-if="error" class="panel-state error">
      <el-alert :title="error" type="warning" :closable="false" show-icon />
      <el-button link type="primary" size="small" @click="emit('retry')">
        重新加载
      </el-button>
    </div>
    <div v-else-if="!hasAnyData" class="panel-state">
      <span class="muted">该周期暂未同步到考勤数据</span>
    </div>

    <!-- 固定免责声明（硬约束：考勤不自动扣分） -->
    <div class="disclaimer">
      <el-icon class="disclaimer-icon"><InfoFilled /></el-icon>
      <span>{{ disclaimer }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  ATTENDANCE_ANOMALY_LABELS,
  ATTENDANCE_ANOMALY_TYPES,
  ATTENDANCE_DISCLAIMER,
  ATTENDANCE_NO_DATA_TYPES,
  type AttendanceAnomalyType,
  type AttendanceSummary,
} from '@/api/attendance'
import { formatPeriodShort } from '@/utils/period'

const props = defineProps<{
  /** 周期（紧凑 202611 或带横杠 2026-11 均可）。 */
  period?: string | null
  /** 考勤统计汇总；为空表示尚未加载。 */
  summary?: AttendanceSummary | null
  /** 记录详情里的参考型考勤证据（作为统计的补充来源）。 */
  referenceEvidence?: Array<{
    type: string
    count: number
    as_of?: string | null
  }>
  loading?: boolean
  error?: string
}>()

const emit = defineEmits<{ (e: 'retry'): void }>()

const disclaimer = ATTENDANCE_DISCLAIMER

/**
 * 展示用的周期标签。
 *
 * ⚠️ **BUG-R6-26**：统一走 `@/utils/period`（唯一权威实现），
 * 不再手写 `slice` 拼接 —— 两种格式进来都产出 `2026-11`。
 */
const periodDisplay = computed(() => {
  if (!props.period) return null
  return formatPeriodShort(props.period, props.period.trim())
})

/** 数据截至时间：优先取统计汇总，其次取证据条目。 */
const asOf = computed(() => {
  if (props.summary?.as_of) return props.summary.as_of
  const evidenceTimes = (props.referenceEvidence || [])
    .map((e) => e.as_of)
    .filter(Boolean) as string[]
  if (!evidenceTimes.length) return null
  return evidenceTimes.sort().slice(-1)[0] ?? null
})

/**
 * 六类异常计数。
 * 优先用统计汇总 `anomaly_counts`；缺失的类别回落到参考型证据 `count`。
 */
const counts = computed<Record<string, number>>(() => {
  const result: Record<string, number> = {}
  const summaryCounts = props.summary?.anomaly_counts || {}
  for (const type of ATTENDANCE_ANOMALY_TYPES) {
    const fromSummary = summaryCounts[type]
    if (typeof fromSummary === 'number') {
      result[type] = fromSummary
      continue
    }
    const fromEvidence = (props.referenceEvidence || [])
      .filter((e) => e.type === type)
      .reduce((sum, e) => sum + (e.count || 0), 0)
    result[type] = fromEvidence
  }
  return result
})

/** 六类异常涉及天数（仅统计汇总有）。 */
const days = computed<Record<string, number | null>>(() => {
  const result: Record<string, number | null> = {}
  const anomalyDays = props.summary?.anomaly_days
  for (const type of ATTENDANCE_ANOMALY_TYPES) {
    const value = anomalyDays?.[type]
    result[type] = typeof value === 'number' ? value : null
  }
  return result
})

const hasAnyData = computed(() => {
  if (props.summary && props.summary.total_records > 0) return true
  return Object.values(counts.value).some((value) => value > 0)
})

const items = computed(() =>
  ATTENDANCE_ANOMALY_TYPES.map((type: AttendanceAnomalyType) => {
    const count = counts.value[type] ?? 0
    const noDataType = ATTENDANCE_NO_DATA_TYPES.includes(type)
    // 暂无真实数据源的类别（overtime / outside）标注【暂无数据】；
    // 其余类别计数为 0 时标注「无异常」，不隐藏任何类别。
    const hasNoData = noDataType && count === 0 && !hasAnyDataForType(type)
    return {
      type,
      label: ATTENDANCE_ANOMALY_LABELS[type],
      count,
      days: days.value[type] ?? null,
      hasNoData,
    }
  }),
)

/** 该类别是否确实同步到过数据。 */
function hasAnyDataForType(type: AttendanceAnomalyType): boolean {
  if (props.summary && props.summary.total_records > 0) {
    const fromSummary = props.summary.anomaly_counts?.[type]
    if (typeof fromSummary === 'number' && fromSummary > 0) return true
  }
  return (props.referenceEvidence || []).some(
    (e) => e.type === type && (e.count || 0) > 0,
  )
}

function formatAsOf(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}
</script>

<style scoped>
.attendance-panel {
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.panel-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0;
  color: #303133;
}

.as-of {
  font-size: 12px;
  color: #909399;
}

.muted {
  color: #c0c4cc;
}

.anomaly-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
}

@media (max-width: 1100px) {
  .anomaly-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.anomaly-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 10px 8px;
  text-align: center;
}

.anomaly-card.is-empty {
  background: #fafafa;
  border-style: dashed;
}

.anomaly-label {
  font-size: 12px;
  color: #606266;
  margin-bottom: 6px;
}

.anomaly-value {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.number {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.1;
  color: #303133;
}

.number.muted {
  color: #c0c4cc;
}

.no-data-tag {
  font-size: 11px;
  color: #e6a23c;
  line-height: 1.3;
}

.no-data-tag.soft {
  color: #b1b3b8;
}

.anomaly-days {
  font-size: 11px;
  color: #a8abb2;
  margin-top: 4px;
}

.panel-state {
  margin-top: 12px;
  font-size: 13px;
}

.panel-state.error {
  display: flex;
  align-items: center;
  gap: 12px;
}

.disclaimer {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px dashed #dcdfe6;
  font-size: 12px;
  color: #909399;
}

.disclaimer-icon {
  color: #e6a23c;
}
</style>
