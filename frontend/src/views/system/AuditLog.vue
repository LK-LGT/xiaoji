<template>
  <section class="page-panel audit-workbench" v-loading="loading">
    <div class="page-header">
      <div>
        <span class="eyebrow">AUDIT TRAIL</span>
        <h1 class="page-title">审计日志</h1>
        <p class="page-subtitle">查看谁在什么时间对业务数据做了什么，技术信息可在详情中按需展开。</p>
      </div>
      <el-button :icon="Download" @click="exportList">导出当前结果</el-button>
    </div>

    <div class="audit-time-shortcuts" aria-label="快捷时间范围">
      <span>时间范围</span>
      <el-button-group>
        <el-button v-for="option in quickRanges" :key="option.value" :type="activeQuickRange === option.value ? 'primary' : 'default'" @click="applyQuickRange(option.value)">
          {{ option.label }}
        </el-button>
      </el-button-group>
    </div>

    <div class="filter-bar audit-filter-bar">
      <el-date-picker v-model="filters.range" type="datetimerange" range-separator="至" start-placeholder="开始时间" end-placeholder="结束时间" value-format="YYYY-MM-DDTHH:mm:ss" @change="activeQuickRange = ''" />
      <el-input v-model="filters.username" clearable placeholder="姓名或账号" />
      <el-select v-model="filters.targetType" clearable filterable placeholder="业务模块">
        <el-option v-for="option in options.targetTypes" :key="option.value" :label="option.label" :value="option.value" />
      </el-select>
      <el-select v-model="filters.action" clearable filterable placeholder="具体动作">
        <el-option-group v-for="group in actionGroups" :key="group.label" :label="group.label">
          <el-option v-for="option in group.options" :key="option.value" :label="option.label" :value="option.value" />
        </el-option-group>
      </el-select>
      <el-radio-group v-model="filters.result" class="audit-result-switch">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="SUCCESS">成功</el-radio-button>
        <el-radio-button value="FAILED">失败</el-radio-button>
      </el-radio-group>
      <el-button type="primary" :icon="Search" @click="search">查询</el-button>
      <el-tooltip content="重置筛选" placement="top">
        <el-button :icon="RefreshLeft" aria-label="重置筛选" @click="reset" />
      </el-tooltip>
    </div>

    <div v-if="activeFilters.length" class="audit-active-filters" aria-label="已生效筛选条件">
      <span>已筛选</span>
      <el-tag v-for="item in activeFilters" :key="item.key" closable effect="plain" @close="clearFilter(item.key)">{{ item.label }}</el-tag>
    </div>

    <div class="table-card audit-table-card">
      <el-table :data="page.records" empty-text="暂无符合条件的审计记录" row-class-name="audit-table-row" @row-click="openDetail">
        <el-table-column label="时间" min-width="176">
          <template #default="{ row }"><time class="audit-time">{{ formatTime(row.createdAt) }}</time></template>
        </el-table-column>
        <el-table-column label="发生的事情" min-width="300">
          <template #default="{ row }">
            <div class="audit-event-cell">
              <strong>{{ row.actionLabel || row.action }}</strong>
              <p>{{ row.targetSummary || targetFallback(row) }}</p>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作人" min-width="170">
          <template #default="{ row }">
            <div class="audit-operator-cell"><strong>{{ row.operatorName || row.username || '系统' }}</strong><small>{{ operatorCaption(row) }}</small></div>
          </template>
        </el-table-column>
        <el-table-column label="结果" min-width="92" align="center">
          <template #default="{ row }"><el-tag :type="row.result === 'SUCCESS' ? 'success' : 'danger'" effect="light">{{ formatAuditResult(row.result) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="" width="48" align="center">
          <template #default><el-icon class="audit-row-arrow"><ArrowRight /></el-icon></template>
        </el-table-column>
      </el-table>
    </div>
    <div class="pagination-row">
      <span>共 {{ page.total }} 条记录</span>
      <el-pagination v-model:current-page="filters.pageNo" :page-size="filters.pageSize" layout="prev, pager, next" :total="page.total" @current-change="load" />
    </div>

    <el-drawer v-model="detailVisible" class="audit-detail-drawer" size="min(680px, 100%)" destroy-on-close>
      <template #header><div><span class="eyebrow">EVENT DETAIL</span><h2>审计记录详情</h2></div></template>
      <template v-if="active">
        <section class="audit-event-summary">
          <span>{{ active.actionLabel || active.action }}</span>
          <h3>{{ active.summary || fallbackSummary(active) }}</h3>
          <div>
            <el-tag :type="active.result === 'SUCCESS' ? 'success' : 'danger'">{{ formatAuditResult(active.result) }}</el-tag>
            <time>{{ formatTime(active.createdAt) }}</time>
          </div>
        </section>

        <dl class="audit-metadata">
          <div><dt>操作人</dt><dd><strong>{{ active.operatorName || active.username || '系统' }}</strong><small>{{ operatorCaption(active) }}</small></dd></div>
          <div><dt>关联对象</dt><dd><strong>{{ active.targetSummary || targetFallback(active) }}</strong><small>{{ active.targetTypeLabel || active.targetType || '未指定对象' }}</small></dd></div>
        </dl>

        <section class="audit-change-section">
          <div class="audit-section-heading"><div><h3>业务明细</h3><p>本次操作记录的关键内容</p></div></div>
          <dl v-if="active.detailItems?.length" class="audit-change-list">
            <div v-for="(item, index) in active.detailItems" :key="`${item.label}-${index}`">
              <dt>{{ item.label }}</dt>
              <dd v-if="item.beforeValue !== undefined || item.afterValue !== undefined" class="audit-diff-value">
                <span>{{ item.beforeValue || '未设置' }}</span><el-icon><ArrowRight /></el-icon><strong>{{ item.afterValue || '未设置' }}</strong>
              </dd>
              <dd v-else>{{ item.value || '未记录' }}</dd>
            </div>
          </dl>
          <div v-else class="audit-no-change">该操作没有记录额外业务明细。</div>
        </section>

        <el-button v-if="active.targetRoute" class="audit-related-link" type="primary" plain :icon="Link" @click="openTarget(active.targetRoute)">查看相关记录</el-button>

        <details class="audit-technical-details">
          <summary>技术信息</summary>
          <dl>
            <div><dt>动作编码</dt><dd>{{ active.action }}</dd></div>
            <div><dt>对象类型</dt><dd>{{ active.targetType || '未记录' }}</dd></div>
            <div><dt>对象 ID</dt><dd>{{ active.targetId || '未记录' }}</dd></div>
            <div><dt>客户端 IP</dt><dd>{{ active.clientIp || '未记录' }}</dd></div>
          </dl>
          <strong>原始详情</strong>
          <pre>{{ formatRawDetail(active.detail) }}</pre>
        </details>
      </template>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ArrowRight, Download, Link, RefreshLeft, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { downloadBlob, exportAudits, getAuditOptions, getAudits, type AuditFilterOption, type AuditItem, type AuditOptions, type PageResult } from '@/api/system'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { formatAuditResult, formatRawDetail, operatorCaption } from './auditPresentation'

type FilterKey = 'range' | 'username' | 'action' | 'targetType' | 'result'

const router = useRouter()
const loading = ref(false)
const detailVisible = ref(false)
const active = ref<AuditItem>()
const activeQuickRange = ref('7d')
const page = reactive<PageResult<AuditItem>>({ records: [], total: 0, pageNo: 1, pageSize: 20 })
const options = reactive<AuditOptions>({ actions: [], targetTypes: [] })
const filters = reactive<{ range: string[]; username: string; action: string; targetType: string; result: string; pageNo: number; pageSize: number }>({
  range: rangeFor('7d'), username: '', action: '', targetType: '', result: '', pageNo: 1, pageSize: 20,
})
const quickRanges = [
  { label: '今天', value: 'today' },
  { label: '近 7 天', value: '7d' },
  { label: '近 30 天', value: '30d' },
  { label: '全部', value: 'all' },
]
const actionGroups = computed(() => {
  const groups = new Map<string, AuditFilterOption[]>()
  options.actions.forEach((option) => {
    const group = option.group || '其他'
    groups.set(group, [...(groups.get(group) || []), option])
  })
  return Array.from(groups, ([label, groupedOptions]) => ({ label, options: groupedOptions }))
})
const activeFilters = computed<Array<{ key: FilterKey; label: string }>>(() => {
  const values: Array<{ key: FilterKey; label: string }> = []
  if (filters.range.length) values.push({ key: 'range', label: rangeLabel() })
  if (filters.username) values.push({ key: 'username', label: `操作人：${filters.username}` })
  if (filters.targetType) values.push({ key: 'targetType', label: `模块：${optionLabel(options.targetTypes, filters.targetType)}` })
  if (filters.action) values.push({ key: 'action', label: `动作：${optionLabel(options.actions, filters.action)}` })
  if (filters.result) values.push({ key: 'result', label: `结果：${formatAuditResult(filters.result)}` })
  return values
})
const autoQuery = useAutoQuery(
  () => [filters.range?.[0], filters.range?.[1], filters.username, filters.action, filters.targetType, filters.result],
  () => { filters.pageNo = 1; return load() },
)

function pad(value: number) { return String(value).padStart(2, '0') }
function localDateTime(value: Date) { return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}` }
function rangeFor(value: string) {
  if (value === 'all') return []
  const end = new Date()
  const start = new Date(end)
  start.setHours(0, 0, 0, 0)
  if (value !== 'today') start.setDate(start.getDate() - (value === '30d' ? 29 : 6))
  return [localDateTime(start), localDateTime(end)]
}
function params() { return { start: filters.range?.[0], end: filters.range?.[1], username: filters.username || undefined, action: filters.action || undefined, targetType: filters.targetType || undefined, result: filters.result || undefined, pageNo: filters.pageNo, pageSize: filters.pageSize } }
async function load() { loading.value = true; try { Object.assign(page, await getAudits(params())) } catch (error) { ElMessage.error(error instanceof Error ? error.message : '审计日志加载失败') } finally { loading.value = false } }
async function loadOptions() { try { Object.assign(options, await getAuditOptions()) } catch { ElMessage.warning('筛选选项加载失败，仍可查看审计记录') } }
function search() { filters.pageNo = 1; void autoQuery.runNow() }
function applyQuickRange(value: string) { activeQuickRange.value = value; filters.range = rangeFor(value) }
async function reset() {
  autoQuery.pause(); activeQuickRange.value = '7d'; filters.range = rangeFor('7d'); filters.username = ''; filters.action = ''; filters.targetType = ''; filters.result = ''; filters.pageNo = 1
  await load(); autoQuery.resume()
}
function clearFilter(key: FilterKey) {
  if (key === 'range') { filters.range = []; activeQuickRange.value = 'all' }
  else if (key === 'username') filters.username = ''
  else if (key === 'action') filters.action = ''
  else if (key === 'targetType') filters.targetType = ''
  else filters.result = ''
}
function openDetail(row: AuditItem) { active.value = row; detailVisible.value = true }
function openTarget(route: string) { detailVisible.value = false; void router.push(route) }
function optionLabel(list: AuditFilterOption[], value: string) { return list.find((item) => item.value === value)?.label || value }
function rangeLabel() { return activeQuickRange.value ? quickRanges.find((item) => item.value === activeQuickRange.value)?.label || '自定义时间' : '自定义时间' }
function formatTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 19) : '未记录' }
function targetFallback(row: AuditItem) { return `${row.targetTypeLabel || row.targetType || '未指定对象'}${row.targetId ? ` #${row.targetId}` : ''}` }
function fallbackSummary(row: AuditItem) { return `${row.operatorName || row.username || '系统'} · ${row.actionLabel || row.action} · ${targetFallback(row)}` }
async function exportList() { try { downloadBlob(await exportAudits(params()), '审计日志.csv') } catch (error) { ElMessage.error(error instanceof Error ? error.message : '审计导出失败') } }

onMounted(async () => { await Promise.all([load(), loadOptions()]); autoQuery.resume() })
</script>
