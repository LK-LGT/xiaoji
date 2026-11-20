/**
 * P2-2 趋势复盘报表面板。
 *
 * 三块内容（可切换）：
 * 1. 跨周期趋势 —— 单个员工多周期的得分与等级变化；
 * 2. 岗位横向 —— 同岗位内的分布与相对位置（**后端自带小样本保护**，
 *    <5 人时前端只显示说明，不显示均值与个人排名）；
 * 3. 风险清单 —— 跨员工的风险名单（**仅 HR / 部门负责人**可见）。
 *
 * 关键呈现约定：
 * - 小样本时**前端也不显示**明细 —— 不能只靠后端，前端要自己再挡一道；
 * - 薪酬导出**不在这个面板里** —— 它是 HR 独立操作，走独立的导出面板。
 */
<template>
  <section class="trend-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">统计复盘</span>
        <h3 class="panel-title">{{ title }}</h3>
        <p class="panel-subtitle">全部为统计描述，不构成评价结论</p>
      </div>
      <div class="head-actions">
        <el-button link type="primary" @click="loadAll">
          刷新
        </el-button>
      </div>
    </header>

    <el-tabs v-model="activeTab" class="tabs">
      <!-- 跨周期趋势 -->
      <el-tab-pane label="跨周期趋势" name="trend">
        <div class="toolbar">
          <el-input
            v-model="employeeIdInput"
            placeholder="员工 ID"
            style="width: 200px"
            clearable
            @keyup.enter="loadTrend"
          />
          <el-button type="primary" :loading="trendLoading" @click="loadTrend">
            查询
          </el-button>
        </div>

        <el-skeleton v-if="trendLoading" :rows="4" animated />

        <el-empty
          v-else-if="!trend"
          :image-size="80"
          description="输入员工 ID 后查询跨周期趋势"
        />

        <div v-else class="trend-body">
          <div class="trend-stats">
            <span class="stat"><em>周期数</em><strong>{{ trend.periodCount }}</strong></span>
            <span class="stat"><em>均分</em><strong>{{ trend.average ?? '—' }}</strong></span>
            <span class="stat"><em>最低</em><strong>{{ trend.min ?? '—' }}</strong></span>
            <span class="stat"><em>最高</em><strong>{{ trend.max ?? '—' }}</strong></span>
            <span class="stat">
              <em>趋势</em>
              <strong>
                <el-tag :type="trendTagType(trend.trend)" size="small" effect="dark">
                  {{ trendLabel(trend.trend) }}
                </el-tag>
              </strong>
            </span>
          </div>

          <!-- 趋势条：纯 CSS，不引图表库 -->
          <div v-if="trend.points.length" class="trend-bars">
            <div
              v-for="point in trend.points"
              :key="point.period"
              class="trend-bar"
            >
              <div class="bar-value">{{ point.total ?? '—' }}</div>
              <div class="bar-track">
                <div
                  class="bar-fill"
                  :style="{ height: barHeight(point.total) }"
                />
              </div>
              <div class="bar-period">{{ formatPeriod(point.period) }}</div>
              <div v-if="point.rating" class="bar-rating">{{ point.rating }}</div>
            </div>
          </div>

          <p class="note">{{ trend.note }}</p>
        </div>
      </el-tab-pane>

      <!-- 岗位横向 -->
      <el-tab-pane label="岗位横向" name="position">
        <div class="toolbar">
          <el-input
            v-model="positionInput"
            placeholder="岗位名称"
            style="width: 200px"
            clearable
            @keyup.enter="loadPosition"
          />
          <el-input
            v-model="periodInput"
            placeholder="周期 如 202611"
            style="width: 180px"
            clearable
            @keyup.enter="loadPosition"
          />
          <el-button type="primary" :loading="positionLoading" @click="loadPosition">
            查询
          </el-button>
        </div>

        <el-skeleton v-if="positionLoading" :rows="4" animated />

        <el-empty
          v-else-if="!position"
          :image-size="80"
          description="输入岗位名称后查询横向对比"
        />

        <div v-else class="position-body">
          <!-- ★ 小样本保护：前端再挡一道 -->
          <el-alert
            v-if="position.smallSample"
            type="warning"
            :closable="false"
            show-icon
            :title="`${position.memberCount} 人，样本不足`"
            :description="position.note"
          />

          <template v-else>
            <div class="trend-stats">
              <span class="stat"><em>人数</em><strong>{{ position.memberCount }}</strong></span>
              <span class="stat"><em>平均分</em><strong>{{ position.distribution.average ?? '—' }}</strong></span>
              <span class="stat"><em>中位数</em><strong>{{ position.distribution.median ?? '—' }}</strong></span>
              <span class="stat"><em>标准差</em><strong>{{ position.distribution.stddev ?? '—' }}</strong></span>
            </div>

            <el-table :data="position.members" stripe size="small" style="width: 100%">
              <el-table-column prop="rank" label="排名" width="70" />
              <el-table-column prop="employee_name" label="员工" min-width="140" />
              <el-table-column prop="total" label="得分" width="90" />
              <el-table-column prop="rating" label="等级" width="80" />
              <el-table-column prop="deviation" label="与均值偏差" width="110">
                <template #default="{ row }">
                  <span :class="deviationClass(row.deviation)">
                    {{ row.deviation > 0 ? '+' : '' }}{{ row.deviation ?? '—' }}
                  </span>
                </template>
              </el-table-column>
            </el-table>

            <p class="note">{{ position.note }}</p>
          </template>
        </div>
      </el-tab-pane>

      <!-- 风险清单 -->
      <el-tab-pane label="风险清单" name="roster">
        <div class="toolbar">
          <el-input
            v-model="departmentInput"
            placeholder="部门 ID（留空为全部）"
            style="width: 220px"
            clearable
            @keyup.enter="loadRoster"
          />
          <el-input
            v-model="periodInput"
            placeholder="周期 如 202611"
            style="width: 180px"
            clearable
            @keyup.enter="loadRoster"
          />
          <el-button type="primary" :loading="rosterLoading" @click="loadRoster">
            查询
          </el-button>
        </div>

        <el-alert
          v-if="rosterError"
          type="warning"
          :closable="false"
          show-icon
          :title="rosterError"
        />

        <el-skeleton v-else-if="rosterLoading" :rows="4" animated />

        <template v-else-if="roster">
          <div class="trend-stats">
            <span class="stat"><em>扫描人数</em><strong>{{ roster.scannedCount }}</strong></span>
            <span class="stat"><em>风险人数</em><strong>{{ roster.riskCount }}</strong></span>
          </div>

          <el-table :data="roster.items" stripe size="small" style="width: 100%">
            <el-table-column label="等级" width="80">
              <template #default="{ row }">
                <el-tag :type="severityType(row.severity)" size="small" effect="dark">
                  {{ severityLabel(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="employee_name" label="员工" min-width="120" />
            <el-table-column prop="department_id" label="部门" width="110" />
            <el-table-column prop="position" label="岗位" width="110" />
            <el-table-column prop="total" label="得分" width="90" />
            <el-table-column label="风险信号" min-width="220">
              <template #default="{ row }">
                <div v-for="signal in row.signals" :key="signal.code" class="signal">
                  <strong>{{ signal.code }}</strong>
                  <span>{{ signal.basis }}</span>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <p class="note">{{ roster.disclaimer }}</p>
        </template>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  trendReportApi,
  type PeriodTrend,
  type PositionComparison,
  type RiskRoster,
} from '@/api/p2'
import { formatPeriodLabel } from '@/utils/period'

const props = withDefaults(
  defineProps<{
    title?: string
    /** 预填员工 ID（从人员列表点进来时用） */
    employeeId?: string
    /** 预填部门 ID */
    departmentId?: string
    /** 预填周期 */
    period?: string
  }>(),
  { title: '趋势复盘报表', employeeId: '', departmentId: '', period: '' },
)

const activeTab = ref('trend')
const employeeIdInput = ref(props.employeeId)
const departmentInput = ref(props.departmentId)
const positionInput = ref('')
const periodInput = ref(props.period)

const trend = ref<PeriodTrend | null>(null)
const position = ref<PositionComparison | null>(null)
const roster = ref<RiskRoster | null>(null)

const trendLoading = ref(false)
const positionLoading = ref(false)
const rosterLoading = ref(false)
const rosterError = ref('')

/** 趋势条的相对高度基准：以 100 分为满格。 */
function barHeight(total: number | null): string {
  if (total === null) return '0%'
  const ratio = Math.max(0, Math.min(100, total)) / 100
  return `${Math.max(ratio * 100, 3)}%`
}

function trendTagType(trend: string): string {
  const map: Record<string, string> = { UP: 'success', DOWN: 'danger', STABLE: 'info' }
  return map[trend] ?? 'info'
}

function trendLabel(trend: string): string {
  const map: Record<string, string> = { UP: '上升', DOWN: '下降', STABLE: '持平' }
  return map[trend] ?? trend
}

function severityType(severity: string): string {
  const map: Record<string, string> = { HIGH: 'danger', MEDIUM: 'warning', LOW: 'info' }
  return map[severity] ?? 'info'
}

function severityLabel(severity: string): string {
  const map: Record<string, string> = { HIGH: '高', MEDIUM: '中', LOW: '低' }
  return map[severity] ?? severity
}

function deviationClass(deviation: number | null): string {
  if (deviation === null) return ''
  if (deviation > 0) return 'dev-up'
  if (deviation < 0) return 'dev-down'
  return ''
}

/** 周期展示：`202611` → `2026年11月`。 */
function formatPeriod(period: string): string {
  return formatPeriodLabel(period)
}

async function loadTrend() {
  if (!employeeIdInput.value) return
  trendLoading.value = true
  try {
    trend.value = await trendReportApi.trend(employeeIdInput.value)
  } catch {
    trend.value = null
  } finally {
    trendLoading.value = false
  }
}

async function loadPosition() {
  if (!positionInput.value) return
  positionLoading.value = true
  try {
    position.value = await trendReportApi.position({
      position: positionInput.value,
      period: periodInput.value || undefined,
    })
  } catch {
    position.value = null
  } finally {
    positionLoading.value = false
  }
}

async function loadRoster() {
  rosterLoading.value = true
  rosterError.value = ''
  try {
    roster.value = await trendReportApi.riskRoster({
      departmentId: departmentInput.value || undefined,
      period: periodInput.value || undefined,
    })
  } catch (err) {
    // 权限不足（403）等情况：明确提示，不静默显示空表
    rosterError.value =
      err instanceof Error ? err.message : '风险清单加载失败（可能无查看权限）'
    roster.value = null
  } finally {
    rosterLoading.value = false
  }
}

async function loadAll() {
  await Promise.all([loadTrend(), loadPosition(), loadRoster()])
}

const hasPreset = computed(() => Boolean(props.employeeId || props.departmentId))

if (hasPreset.value) {
  void loadAll()
}

defineExpose({ loadAll })
</script>

<style scoped>
.trend-panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 8px;
}

.head-text {
  flex: 1;
}

.eyebrow {
  display: block;
  font-size: 11px;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 4px 0;
  color: #303133;
}

.panel-subtitle {
  font-size: 13px;
  color: #606266;
  margin: 0;
}

.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.trend-stats {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat em {
  font-size: 11px;
  color: #909399;
  font-style: normal;
}

.stat strong {
  font-size: 18px;
  color: #303133;
}

.trend-bars {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  height: 160px;
  padding: 8px 0;
  border-bottom: 1px solid #ebeef5;
  overflow-x: auto;
}

.trend-bar {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 56px;
  height: 100%;
  justify-content: flex-end;
}

.bar-value {
  font-size: 11px;
  color: #606266;
  margin-bottom: 2px;
}

.bar-track {
  flex: 1;
  width: 26px;
  display: flex;
  align-items: flex-end;
}

.bar-fill {
  width: 100%;
  background: #409eff;
  border-radius: 3px 3px 0 0;
  transition: height 0.3s;
}

.bar-period {
  font-size: 11px;
  color: #909399;
  margin-top: 6px;
}

.bar-rating {
  font-size: 11px;
  color: #409eff;
  font-weight: 600;
}

.note {
  font-size: 12px;
  color: #909399;
  margin: 12px 0 0 0;
  line-height: 1.7;
}

.signal {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 4px;
}

.signal strong {
  font-size: 11px;
  color: #606266;
}

.signal span {
  font-size: 11px;
  color: #909399;
}

.dev-up {
  color: #67c23a;
}

.dev-down {
  color: #f56c6c;
}
</style>
