<template>
  <section class="result-archive">
    <div class="archive-toolbar" role="search" aria-label="成果档案筛选">
      <div class="archive-filter archive-filter--type">
        <el-select v-model="filters.type" aria-label="成果类型" placeholder="全部类型" clearable>
          <el-option label="全部类型" value="" />
          <el-option label="月度绩效成果" value="MONTH" />
          <el-option label="周度执行成果" value="WEEK" />
        </el-select>
      </div>
      <div class="archive-filter archive-filter--month">
        <el-date-picker v-model="filters.month" type="month" value-format="YYYY-MM" format="YYYY年MM月" placeholder="全部月份" clearable aria-label="成果月份" />
      </div>
      <div class="archive-filter archive-filter--status">
        <el-select v-model="filters.status" aria-label="成果状态" placeholder="全部状态" clearable>
          <el-option label="全部状态" value="" />
          <el-option label="审批中" value="SUBMITTED" />
          <el-option label="已确认/已通过" value="COMPLETED" />
          <el-option label="已退回" value="REJECTED" />
        </el-select>
      </div>
      <div class="archive-filter archive-filter--keyword">
        <el-input v-model="filters.keyword" clearable placeholder="搜索任务名称或成果内容" aria-label="搜索成果" @keyup.enter="applyFilters">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </div>
      <div class="archive-filter-actions">
        <el-button type="primary" @click="applyFilters">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>
    </div>
    <el-alert v-if="errorMessage" class="archive-alert" type="warning" :closable="false" show-icon :title="errorMessage"><el-button link type="primary" @click="loadArchive">重试</el-button></el-alert>
    <el-skeleton v-if="loading" :rows="8" animated />
    <div v-else-if="rows.length" class="archive-table-wrap">
      <div v-for="group in monthGroups" :key="group.key" class="archive-month-group">
        <header class="archive-month-header">
          <strong>{{ group.label }}</strong>
          <span>{{ group.rows.length }} 项成果</span>
        </header>
        <div class="archive-table-scroll">
          <table class="archive-table" :aria-label="`${group.label}成果档案`">
            <colgroup>
              <col class="archive-col--type" />
              <col class="archive-col--task" />
              <col class="archive-col--period" />
              <col class="archive-col--progress" />
              <col class="archive-col--status" />
              <col class="archive-col--updated" />
              <col class="archive-col--action" />
            </colgroup>
            <thead>
              <tr><th scope="col">类型</th><th scope="col">档案名称</th><th scope="col">所属周期</th><th scope="col">成果进度</th><th scope="col">档案状态</th><th scope="col">更新时间</th><th scope="col">操作</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="row in group.rows"
                :key="row.key"
                tabindex="0"
                @click="openDetail(row)"
                @keydown.enter="openDetail(row)"
                @keydown.space.prevent="openDetail(row)"
              >
                <td><el-tag size="small" effect="plain">{{ row.type === 'MONTH' ? '月度' : '周度' }}</el-tag></td>
                <td class="archive-task-cell">
                  <strong class="row-title">{{ row.title }}</strong>
                  <small v-if="row.description">{{ row.description }}</small>
                </td>
                <td class="archive-period-cell">{{ row.periodLabel }}</td>
                <td class="archive-progress-cell">{{ row.resultCount }} / {{ row.taskCount }}</td>
                <td>
                  <el-tag v-if="row.type === 'MONTH'" size="small" effect="plain" :type="archiveStatusMeta(row).type">{{ archiveStatusMeta(row).label }}</el-tag>
                  <ResultStatusTag v-else :status="row.status" />
                </td>
                <td class="archive-updated-cell">{{ dateTimeText(row.updatedAt) }}</td>
                <td class="archive-action-cell"><el-button link type="primary" :icon="View" @click.stop="openDetail(row)">查看</el-button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <footer class="archive-pagination">
        <el-pagination v-model:current-page="pageNo" :page-size="pageSize" layout="prev, pager, next, total" :total="filteredRows.length" @current-change="changePage" />
      </footer>
    </div>
    <div v-else class="archive-empty"><el-icon><FolderOpened /></el-icon><strong>{{ hasFilters ? '没有匹配的成果记录' : '还没有成果档案' }}</strong><span>{{ hasFilters ? '请清空筛选条件后重试。' : '成果提交并完成审批后，会在这里形成档案。' }}</span><el-button v-if="hasFilters" link type="primary" @click="resetFilters">清空筛选</el-button></div>
  </section>
</template>

<script setup lang="ts">
import { FolderOpened, Search, View } from '@element-plus/icons-vue'
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEmployeeResultSubmitOptionsApi, type EmployeeResultSubmitOptionsResp } from '@/api/employee'
import { getWeekResultSubmitOptionsApi, type WeekResultSubmitOptions } from '@/api/weekResult'
import { groupRowsByMonth } from '@/utils/monthGroups'
import ResultStatusTag from '@/components/results/ResultStatusTag.vue'
import { buildMonthResultArchives, monthArchiveStatusMeta, type MonthArchiveStatus } from './resultArchiveModel'

type ArchiveStatus = MonthArchiveStatus | 'APPROVED'
// BUG-R6-14：成果 / 计划 / 任务 ID 都是字符串主键。
interface ArchiveRow { key: string; type: 'MONTH' | 'WEEK'; resultId: string; planId: string; taskId: string; title: string; description: string; periodKey: string; periodLabel: string; status: ArchiveStatus; updatedAt: string; resultDate: string; resultCount: number; taskCount: number; searchText: string }
const ARCHIVE_SCROLL_KEY = 'employee-result-archive-scroll'
const route = useRoute(); const router = useRouter(); const resultBasePath = computed(() => route.path.startsWith('/leader/') ? '/leader/results' : '/employee/results'); const loading = ref(false); const errorMessage = ref(''); const pageNo = ref(positiveInt(route.query.archivePage)); const pageSize = 10
const filters = reactive({
  type: ['MONTH', 'WEEK'].includes(String(route.query.archiveType || '')) ? String(route.query.archiveType) : '',
  month: /^\d{4}-\d{2}$/.test(String(route.query.archiveMonth || '')) ? String(route.query.archiveMonth) : '',
  status: ['SUBMITTED', 'COMPLETED', 'REJECTED'].includes(String(route.query.archiveStatus || '')) ? String(route.query.archiveStatus) : '',
  keyword: String(route.query.archiveKeyword || '').slice(0, 100),
}); const monthOptions = ref<EmployeeResultSubmitOptionsResp | null>(null); const weekOptions = ref<WeekResultSubmitOptions | null>(null)
const allRows = computed<ArchiveRow[]>(() => {
  const result: ArchiveRow[] = []; const months = monthOptions.value
  if (months) {
    for (const archive of buildMonthResultArchives(months)) {
      const description = [archive.employeeName, archive.projectName].filter(Boolean).join(' · ')
      result.push({ key: archive.key, type: 'MONTH', resultId: '', planId: archive.planId, taskId: '', title: archive.title, description, periodKey: archive.planMonth, periodLabel: monthText(archive.planMonth), status: archive.status, updatedAt: archive.updatedAt, resultDate: `${archive.planMonth}-01`, resultCount: archive.counts.result, taskCount: archive.counts.total, searchText: archive.searchText })
    }
  }
  const weeks = weekOptions.value
  if (weeks) for (const option of weeks.plans) { const latest = option.latestResult; if (!latest || !['SUBMITTED', 'APPROVED', 'REJECTED'].includes(latest.status)) continue; const description = latest.content || latest.completionStatus || ''; result.push({ key: `WEEK:${latest.id}`, type: 'WEEK', resultId: latest.id, planId: option.weekPlanId, taskId: option.task.id, title: option.task.content, description, periodKey: option.weekStart.slice(0, 7), periodLabel: `${option.weekStart} 至 ${option.weekEnd}`, status: latest.status as ArchiveStatus, updatedAt: latest.submitAt, resultDate: latest.resultDate, resultCount: 1, taskCount: 1, searchText: `${option.task.content} ${description}`.toLowerCase() }) }
  return result.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)))
})
const filteredRows = computed(() => allRows.value.filter((row) => { if (filters.type && row.type !== filters.type) return false; if (filters.month && row.periodKey !== filters.month) return false; if (filters.status === 'SUBMITTED' && !['SUBMITTED', 'PARTIAL'].includes(row.status)) return false; if (filters.status === 'COMPLETED' && !['CONFIRMED', 'APPROVED'].includes(row.status)) return false; if (filters.status === 'REJECTED' && row.status !== 'REJECTED') return false; if (filters.keyword && !row.searchText.includes(filters.keyword.trim().toLowerCase())) return false; return true }))
const rows = computed(() => filteredRows.value.slice((pageNo.value - 1) * pageSize, pageNo.value * pageSize)); const monthGroups = computed(() => groupRowsByMonth(rows.value, (row) => row.resultDate)); const hasFilters = computed(() => Boolean(filters.type || filters.month || filters.status || filters.keyword))
function dateTimeText(value?: string) { return value ? value.replace('T', ' ').slice(0, 16) : '--' }
function monthText(value: string) { const [year, month] = value.split('-'); return year && month ? `${year}年${Number(month)}月` : value }
function archiveStatusMeta(row: ArchiveRow) { return monthArchiveStatusMeta(row.status as MonthArchiveStatus) }
function positiveInt(value: unknown) { const parsed = Number(value); return Number.isInteger(parsed) && parsed > 0 ? parsed : 1 }
function archiveQuery() { return { view: 'archive', ...(filters.type ? { archiveType: filters.type } : {}), ...(filters.month ? { archiveMonth: filters.month } : {}), ...(filters.status ? { archiveStatus: filters.status } : {}), ...(filters.keyword.trim() ? { archiveKeyword: filters.keyword.trim() } : {}), ...(pageNo.value > 1 ? { archivePage: String(pageNo.value) } : {}) } }
function syncRoute() { void router.replace({ path: resultBasePath.value, query: archiveQuery() }) }
function openDetail(row: ArchiveRow) { sessionStorage.setItem(ARCHIVE_SCROLL_KEY, String(window.scrollY)); const id = row.type === 'MONTH' ? row.planId : row.resultId; void router.push({ path: `${resultBasePath.value}/archive/${row.type}/${id}`, query: { ...archiveQuery(), planId: String(row.planId), taskId: String(row.taskId), ...(row.type === 'MONTH' ? { archiveScope: 'plan' } : {}) } }) }
function applyFilters() { pageNo.value = 1; syncRoute() }
function resetFilters() { Object.assign(filters, { type: '', month: '', status: '', keyword: '' }); pageNo.value = 1; syncRoute() }
function changePage() { syncRoute(); window.scrollTo({ top: 0 }) }
async function restoreScroll() { await nextTick(); const value = Number(sessionStorage.getItem(ARCHIVE_SCROLL_KEY)); if (Number.isFinite(value) && value > 0) window.scrollTo({ top: value }); sessionStorage.removeItem(ARCHIVE_SCROLL_KEY) }
async function loadArchive() { loading.value = true; errorMessage.value = ''; let loaded = false; try { const [months, weeks] = await Promise.all([getEmployeeResultSubmitOptionsApi(), getWeekResultSubmitOptionsApi()]); monthOptions.value = months; weekOptions.value = weeks; loaded = true } catch (error) { errorMessage.value = error instanceof Error ? error.message : '成果档案加载失败，请重试' } finally { loading.value = false } if (loaded) await restoreScroll() }
onMounted(loadArchive)
</script>

<style scoped>
.result-archive { padding-top:16px; color:#263a34; }
.archive-toolbar { display:grid; grid-template-columns:160px 170px 170px minmax(260px,1fr) auto; align-items:center; gap:10px; padding:10px 12px; border:1px solid #d8e1dc; border-radius:4px; background:#f6f8f6; }
.archive-filter { min-width:0; }
.archive-filter :deep(.el-select),.archive-filter :deep(.el-input),.archive-filter :deep(.el-date-editor) { width:100%; min-width:0; }
.archive-filter-actions { display:flex; align-items:center; gap:8px; white-space:nowrap; }
.archive-filter-actions :deep(.el-button) { min-width:72px; }
.archive-filter-actions :deep(.el-button + .el-button) { margin-left:0; }
.archive-alert { margin-top:14px; }
.archive-table-wrap { margin-top:16px; background:#fff; }
.archive-month-group { margin:0; background:#fff; }
.archive-month-group + .archive-month-group { margin-top:16px; }
.archive-month-header { display:flex; align-items:center; justify-content:space-between; min-height:42px; padding:0 10px 0 12px; border-bottom:1px solid #d8e1dc; border-left:3px solid #2d776c; background:#fff; }
.archive-month-header strong { font-size:17px; font-weight:700; line-height:1.3; }
.archive-month-header span { color:#718078; font-size:12px; }
.archive-table-scroll { overflow-x:auto; background:#fff; }
.archive-table { width:100%; min-width:940px; border-collapse:collapse; table-layout:fixed; background:#fff; font-size:13px; }
.archive-col--type { width:80px; }
.archive-col--task { width:auto; }
.archive-col--period { width:190px; }
.archive-col--progress { width:100px; }
.archive-col--status { width:110px; }
.archive-col--updated { width:155px; }
.archive-col--action { width:82px; }
.archive-table th,.archive-table td { padding:10px 12px; border-bottom:1px solid #dfe6e1; text-align:left; vertical-align:middle; }
.archive-table th { height:40px; padding-top:0; padding-bottom:0; color:#52665e; background:#f3f6f3; font-size:12px; font-weight:650; }
.archive-table tbody tr { height:62px; cursor:pointer; transition:background-color .16s ease; }
.archive-table tbody tr:hover { background:#f5f9f6; }
.archive-table tbody tr:focus-visible { position:relative; z-index:1; outline:2px solid #2d776c; outline-offset:-2px; background:#f5f9f6; }
.archive-task-cell { min-width:0; }
.row-title { display:-webkit-box; overflow:hidden; color:#20352e; font-size:14px; font-weight:650; line-height:20px; word-break:break-word; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
.archive-table td small { display:block; overflow:hidden; margin-top:3px; color:#7b8983; line-height:18px; text-overflow:ellipsis; white-space:nowrap; }
.archive-period-cell,.archive-progress-cell,.archive-updated-cell { color:#42564f; white-space:nowrap; }
.archive-progress-cell { font-variant-numeric:tabular-nums; font-weight:650; }
.archive-updated-cell { font-variant-numeric:tabular-nums; }
.archive-action-cell { text-align:right; }
.archive-pagination { display:flex; justify-content:flex-end; padding-top:14px; background:#fff; }
.archive-empty { display:grid; min-height:310px; place-items:center; align-content:center; gap:9px; color:#75847d; text-align:center; }
.archive-empty .el-icon { color:#2d776c; font-size:30px; }
.archive-empty strong { color:#30463f; }
@media (max-width:1180px) {
  .archive-toolbar { grid-template-columns:160px 170px minmax(170px,1fr) auto; }
  .archive-filter--keyword { grid-column:1 / 4; grid-row:2; }
  .archive-filter-actions { grid-column:4; grid-row:2; justify-content:flex-end; }
}
</style>
