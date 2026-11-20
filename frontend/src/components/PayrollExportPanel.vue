/**
 * P2-2 薪酬导出面板（仅 HR）。
 *
 * ⚠️ 这是全系统**敏感度最高**的一屏。三条硬约束：
 * 1. 姓名默认**不导出** —— 需要时由 HR 显式勾选，且勾选状态会写进说明里；
 * 2. 系数是**规则建议值**，不是调薪承诺 —— 导出的每一行都要带上这句话；
 * 3. 导出前必须让 HR 看到**将要导出多少行**、**将以什么形式**。
 *
 * 导出走浏览器下载，不落在前端内存里长期保存。
 */
<template>
  <section class="payroll-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">敏感数据</span>
        <h3 class="panel-title">薪酬建议导出</h3>
        <p class="panel-subtitle">系数为规则建议值，不构成调薪承诺</p>
      </div>
    </header>

    <el-alert
      type="error"
      :closable="false"
      show-icon
      title="最高敏感级数据"
      description="导出的系数来自「等级 → 绩效系数」的固定映射，是系统建议值。实际调薪必须走薪酬审批流程，本导出不替代审批。"
      class="safety-note"
    />

    <div class="toolbar">
      <el-input
        v-model="departmentId"
        placeholder="部门 ID（留空为全部）"
        style="width: 220px"
        clearable
      />
      <el-input
        v-model="period"
        placeholder="周期 如 202611"
        style="width: 180px"
        clearable
      />
      <el-checkbox v-model="includeNames">包含员工姓名</el-checkbox>
      <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      <el-button :disabled="!rows.length" @click="download">导出 CSV</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="4" animated />

    <template v-else-if="rows.length">
      <div class="stat-row">
        <span class="stat"><em>导出行数</em><strong>{{ rows.length }}</strong></span>
        <span class="stat">
          <em>姓名</em>
          <strong>{{ includeNames ? '包含' : '不包含' }}</strong>
        </span>
        <span class="stat">
          <em>缺分数</em>
          <strong :class="{ 'stat-warn': unscoredCount > 0 }">{{ unscoredCount }}</strong>
        </span>
      </div>

      <!--
        ⚠️ 缺分数的行**必须显眼**：
        这些记录有确认等级（系数照样给了），但算分没完成。
        混在正常行里，HR 可能直接拿系数去批调薪 ——
        那等于拿一个「没算出来的数」当依据。
      -->
      <el-alert
        v-if="unscoredCount > 0"
        type="warning"
        :closable="false"
        show-icon
        :title="`${unscoredCount} 条已确认评级但缺分数`"
        description="这些记录的系数仅为等级映射值，结算前须补齐算分与举证，不能直接作为调薪依据。"
        class="unscored-alert"
      />

      <el-table :data="rows" stripe size="small" style="width: 100%">
        <el-table-column prop="employee_key" label="员工标识" width="110" />
        <el-table-column v-if="includeNames" prop="employee_name" label="姓名" width="120" />
        <el-table-column prop="department_id" label="部门" width="110" />
        <el-table-column prop="position" label="岗位" width="110" />
        <el-table-column label="得分" width="110">
          <template #default="{ row }">
            <span v-if="row.scored === false" class="unscored-cell">
              未算分
              <el-tooltip content="该记录已确认评级，但算分未完成（主管评分不因算分失败回滚）。" placement="top">
                <span class="unscored-mark">?</span>
              </el-tooltip>
            </span>
            <span v-else>{{ row.total ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="rating" label="等级" width="80" />
        <el-table-column prop="coefficient" label="建议系数" width="100" />
      </el-table>

      <p class="note">{{ payload?.disclaimer }}</p>
      <p class="note">{{ payload?.privacyNote }}</p>
    </template>

    <el-empty v-else :image-size="80" description="暂无数据，或该周期没有已确认评级的记录" />
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { trendReportApi, type PayrollExport } from '@/api/p2'

const departmentId = ref('')
const period = ref('')
const includeNames = ref(false)
const loading = ref(false)
const payload = ref<PayrollExport | null>(null)

const rows = computed(() => payload.value?.rows ?? [])
const unscoredCount = computed(() => payload.value?.unscoredCount ?? 0)

async function load() {
  loading.value = true
  try {
    payload.value = await trendReportApi.payrollExport({
      departmentId: departmentId.value || undefined,
      period: period.value || undefined,
      includeNames: includeNames.value,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导出数据加载失败')
    payload.value = null
  } finally {
    loading.value = false
  }
}

/** 下载 CSV。文件名带周期与时间戳，避免覆盖。 */
function download() {
  const data = payload.value
  if (!data || !data.rows.length) return

  const header = ['员工标识', '部门', '岗位', '得分', '等级', '建议系数', '算分状态']
  if (includeNames.value) header.splice(1, 0, '姓名')

  const lines = [header.join(',')]
  for (const row of data.rows) {
    const cells: string[] = [row.employee_key]
    if (includeNames.value) cells.push(row.employee_name ?? '')
    cells.push(
      row.department_id ?? '',
      row.position ?? '',
      // 缺分数写「未算分」而不是留空 —— 留空在 Excel 里像 0，会被误当依据
      row.scored === false ? '未算分' : String(row.total ?? ''),
      row.rating ?? '',
      String(row.coefficient ?? ''),
      row.scored === false ? '缺分数' : '已算分',
    )
    lines.push(cells.map(csvEscape).join(','))
  }
  // 免责说明随文件一起走 —— 脱离系统上下文后仍然有效
  lines.push('')
  lines.push(`# ${data.disclaimer}`)

  const blob = new Blob([`﻿${lines.join('\n')}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `payroll-${data.period ?? 'all'}-${Date.now()}.csv`
  link.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已导出')
}

function csvEscape(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replace(/"/g, '""')}"`
  return value
}
</script>

<style scoped>
.payroll-panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-left: 3px solid #f56c6c;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
}

.panel-head {
  margin-bottom: 12px;
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

.safety-note {
  margin-bottom: 14px;
}

.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  flex-wrap: wrap;
  align-items: center;
}

.stat-row {
  display: flex;
  gap: 20px;
  margin-bottom: 12px;
  flex-wrap: wrap;
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

.stat-warn {
  color: #e6a23c;
}

.unscored-alert {
  margin-bottom: 12px;
}

.unscored-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #e6a23c;
  font-size: 12px;
}

.unscored-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #e6a23c;
  color: #fff;
  font-size: 10px;
  cursor: help;
}

.note {
  font-size: 12px;
  color: #909399;
  margin: 10px 0 0 0;
  line-height: 1.7;
}
</style>
