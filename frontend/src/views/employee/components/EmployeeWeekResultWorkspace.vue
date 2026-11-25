<template>
  <section class="week-result-workspace">
    <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />
    <el-skeleton v-if="loading" class="week-result-loading" :rows="8" animated />

    <template v-else-if="!errorMessage">
      <section class="week-result-month-board">
        <div class="week-result-board-toolbar">
          <div>
            <strong>周计划成果</strong>
            <span>按月份查看已审批周计划，展开后直接填写成果。</span>
          </div>
          <div class="week-result-toolbar-actions">
            <el-tooltip content="刷新数据" placement="bottom">
              <el-button circle :icon="Refresh" aria-label="刷新周成果数据" @click="loadOptions" />
            </el-tooltip>
            <el-button @click="router.push(resultListPath)">返回成果记录</el-button>
          </div>
        </div>

        <div v-if="monthGroups.length" class="week-result-month-list">
          <article v-for="group in pagedMonthGroups" :key="group.key" class="week-result-month-group">
            <div class="week-result-month-header">
              <button
                type="button"
                class="week-result-month-toggle"
                :aria-expanded="expandedMonthKeys.has(group.key)"
                :aria-controls="`week-result-month-${group.key}`"
                @click="toggleMonth(group.key)"
              >
                <el-icon><ArrowDown v-if="expandedMonthKeys.has(group.key)" /><ArrowRight v-else /></el-icon>
                <strong>{{ group.label }}周计划</strong>
                <span>{{ group.rows.length }} 份周计划</span>
              </button>
              <el-button type="primary" :icon="EditPen" @click="expandMonthForEdit(group.key)">编辑</el-button>
            </div>

            <div :id="`week-result-month-${group.key}`" v-show="expandedMonthKeys.has(group.key)" class="week-result-month-detail">
              <div class="week-result-edit-wrap">
                <table class="week-result-edit-table" aria-label="周计划成果编辑表">
                  <thead>
                    <tr>
                      <th class="edit-seq">序号</th>
                      <th class="edit-work">工作计划内容</th>
                      <th class="edit-completion">完成情况</th>
                      <th class="edit-deliverable">交付物</th>
                      <th class="edit-remark">备注</th>
                      <th class="edit-period">统计周期</th>
                      <th class="edit-evidence">成果提交（选填）</th>
                      <th class="edit-action">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, index) in group.rows" :key="row.key" :class="{ 'is-active': activeRowKey === row.key, 'is-readonly': rowLocked(row) }" @click="activeRowKey = row.key">
                      <td class="cell-center">{{ index + 1 }}</td>
                      <td class="work-content">{{ row.content }}</td>
                      <td class="sheet-edit-cell"><el-input v-model="row.completionStatus" type="textarea" :rows="4" maxlength="1000" resize="none" placeholder="填写实际完成情况" :disabled="rowLocked(row)" /></td>
                      <td class="sheet-edit-cell"><el-input v-model="row.deliverable" type="textarea" :rows="4" maxlength="500" resize="none" placeholder="填写实际交付物" :disabled="rowLocked(row)" /></td>
                      <td class="sheet-edit-cell"><el-input v-model="row.remark" type="textarea" :rows="4" maxlength="2000" resize="none" placeholder="选填" :disabled="rowLocked(row)" /></td>
                      <td class="cell-center">{{ periodText(row.weekStart, row.weekEnd) }}</td>
                      <td class="cell-center">
                        <label v-if="!rowLocked(row)" class="week-result-file-picker" :for="`week-result-file-table-${row.task.id}`"><el-icon><Paperclip /></el-icon>{{ row.file ? '已选择' : '选择文件' }}</label>
                        <span v-else-if="row.hasEvidence" class="evidence-state is-uploaded">已上传</span>
                        <span v-else class="muted-text">未上传</span>
                        <input :id="`week-result-file-table-${row.task.id}`" class="visually-hidden" type="file" :accept="uploadAccept" :disabled="rowLocked(row)" @change="handleFileChange(row, $event)" />
                        <small v-if="row.file" class="selected-file-name">{{ row.file.name }}</small>
                      </td>
                      <td class="cell-center">
                        <el-tag :type="statusMeta(row.status).type" effect="plain">{{ statusMeta(row.status).label }}</el-tag>
                        <el-button v-if="!rowLocked(row)" link type="primary" :loading="submittingKey === row.key" @click.stop="submitRow(row)">提交成果</el-button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </article>

          <div v-if="monthGroups.length > monthPageSize" class="week-result-pagination">
            <el-pagination v-model:current-page="monthPage" :page-size="monthPageSize" layout="prev, pager, next, total" :total="monthGroups.length" />
          </div>
        </div>

        <div v-else class="week-result-empty">
          <el-icon><Check /></el-icon>
          <strong>暂无可填写成果的周计划</strong>
          <span>周计划审批通过后，会在这里按月份展示。</span>
        </div>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { ArrowDown, ArrowRight, Check, EditPen, Paperclip, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getWeekResultSubmitOptionsApi, submitWeekResultApi, type WeekResultRecord, type WeekResultSubmitOption } from '@/api/weekResult'
import { groupRowsByMonth, paginateMonthGroups } from '@/utils/monthGroups'
import { buildFileAcceptAttribute, fileExtension as fileExtensionUtil, isAcceptedFile } from '@/utils/fileUpload'
import { periodMonthKey } from '@/utils/period'
// ⚠️ **纯逻辑已抽到 `@/utils/weekResultWorkspace`**（CI-5 阶段 2 第五轮）⇒ 这里只做转发。
import {
  buildEditableWeekResultRow,
  formatWeekResultPeriod,
  isWeekResultRowLocked,
  resolveFocusWeekResultRow,
  resolveMonthEditableWeekResultRow,
  resolveWeekMonthFromQuery,
  resolveWeekPlanIdFromQuery,
  resolveWeekResultCompletion,
  resolveWeekResultDeliverable,
  resolveWeekResultStatusMeta,
  resolveWeekResultValidationMessage,
  validateWeekResultRow,
} from '@/utils/weekResultWorkspace'

const monthPageSize = 10

interface WeekResultRow extends WeekResultSubmitOption {
  key: string
  planMonth: string
  content: string
  status: string
}

interface EditableWeekResultRow extends WeekResultRow {
  completionStatus: string
  deliverable: string
  remark: string
  hasEvidence: boolean
  file: File | null
}

const route = useRoute()
const router = useRouter()
const isLeaderWorkspace = computed(() => route.path.startsWith('/leader/'))
const resultListPath = computed(() => isLeaderWorkspace.value ? '/leader/results' : '/employee/results')
const loading = ref(false)
const errorMessage = ref('')
const submittingKey = ref('')
const monthPage = ref(1)
const expandedMonthKeys = reactive(new Set<string>())
const editableRows = ref<EditableWeekResultRow[]>([])
const activeRowKey = ref('')
// ⚠️ BUG-R6-19：默认值必须与后端 `ACCEPTED_FILE_EXTENSIONS` 一致（扩展名，不是 MIME）。
const options = reactive({ plans: [] as WeekResultSubmitOption[], acceptedFileTypes: ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'png', 'jpg', 'jpeg'], maxFileSizeMb: 20 })

const focusWeekMonth = computed(() => resolveWeekMonthFromQuery(route.query))
const uploadAccept = computed(() => buildFileAcceptAttribute(options.acceptedFileTypes))
const rows = computed<WeekResultRow[]>(() => options.plans.map((plan) => ({
  ...plan,
  key: `${plan.weekPlanId}-${plan.task.id}`,
  // ⚠️ **BUG-R6-26**：统一走 `periodMonthKey`（不再手写 `slice(0, 7)`）。
  planMonth: periodMonthKey(plan.weekStart),
  content: plan.task.content,
  status: plan.latestResult?.status || 'NOT_SUBMITTED',
})))
const monthGroups = computed(() => groupRowsByMonth(editableRows.value, (row) => row.planMonth))
const pagedMonthGroups = computed(() => paginateMonthGroups(monthGroups.value, monthPage.value, monthPageSize))

function toggleMonth(key: string) { expandedMonthKeys.has(key) ? expandedMonthKeys.delete(key) : expandedMonthKeys.add(key) }
function expandMonthForEdit(key: string) {
  expandedMonthKeys.add(key)
  const firstEditableRow = resolveMonthEditableWeekResultRow(editableRows.value, key)
  activeRowKey.value = firstEditableRow?.key || activeRowKey.value
}
function periodText(start: string, end: string) { return formatWeekResultPeriod(start, end) }
function resultCompletion(result?: WeekResultRecord | null) {
  return resolveWeekResultCompletion(result)
}
function resultDeliverable(row: WeekResultRow) { return resolveWeekResultDeliverable(row) }
function statusMeta(status: string) {
  return resolveWeekResultStatusMeta(status)
}
function rowLocked(row: EditableWeekResultRow) { return isWeekResultRowLocked(row) }

function rebuildEditableRows() {
  editableRows.value = rows.value.map((row) => buildEditableWeekResultRow(row))
  // BUG-R6-14：周计划 ID 是字符串主键，不能用 `Number(...)` 强转
  // （NaN 永远匹配不到行）。
  const focusPlanId = resolveWeekPlanIdFromQuery(route.query)
  const focusRow = resolveFocusWeekResultRow(editableRows.value, focusPlanId)
  activeRowKey.value = focusRow?.key || ''
}

function resetMonthView() {
  monthPage.value = 1
  expandedMonthKeys.clear()
  const focusPlanId = resolveWeekPlanIdFromQuery(route.query)
  const focusRow = editableRows.value.find((row) => row.weekPlanId === focusPlanId)
  const monthToOpen = focusWeekMonth.value || focusRow?.planMonth || ''
  if (!monthToOpen) return

  const groupIndex = monthGroups.value.findIndex((group) => group.key === monthToOpen)
  if (groupIndex < 0) return
  monthPage.value = Math.floor(groupIndex / monthPageSize) + 1
  expandedMonthKeys.add(monthToOpen)
}

function fileExtension(fileName: string) { return fileExtensionUtil(fileName) }
function validateFile(file: File) {
  // ⚠️ BUG-R6-19：按扩展名统一归一后再比对（MIME 输入也能正确处理）。
  if (!isAcceptedFile(file.name, options.acceptedFileTypes)) {
    ElMessage.warning('文件类型不支持，请上传 PDF、Word、Zip 或图片文件')
    return false
  }
  if (file.size > options.maxFileSizeMb * 1024 * 1024) {
    ElMessage.warning(`文件大小不能超过 ${options.maxFileSizeMb} MB`)
    return false
  }
  return true
}
function handleFileChange(row: EditableWeekResultRow, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] || null
  if (file && !validateFile(file)) { input.value = ''; return }
  row.file = file
  activeRowKey.value = row.key
}
function validateRow(row: EditableWeekResultRow) {
  const error = validateWeekResultRow(row)
  if (error) { ElMessage.warning(resolveWeekResultValidationMessage(error)); return false }
  return !row.file || validateFile(row.file)
}
async function submitRow(row: EditableWeekResultRow) {
  if (submittingKey.value || !validateRow(row)) return
  try {
    await ElMessageBox.confirm('确认提交当前周计划成果？待审批期间不能重复提交，驳回后可修改并重新提交。', '提交成果', { type: 'warning', confirmButtonText: '确认提交', cancelButtonText: '继续编辑' })
  } catch { return }
  submittingKey.value = row.key
  errorMessage.value = ''
  try {
    const payload = new FormData()
    payload.append('completionRate', '100')
    payload.append('completionStatus', row.completionStatus.trim())
    payload.append('deliverable', row.deliverable.trim())
    payload.append('remark', row.remark.trim())
    payload.append('content', row.completionStatus.trim())
    if (row.file) payload.append('file', row.file)
    const result = await submitWeekResultApi(row.weekPlanId, row.task.id, payload)
    ElMessage.success(result.message || '周计划成果已提交')
    await loadOptions()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '周计划成果提交失败，请稍后重试'
  } finally { submittingKey.value = '' }
}

async function loadOptions() {
  loading.value = true
  errorMessage.value = ''
  try {
    Object.assign(options, await getWeekResultSubmitOptionsApi())
    rebuildEditableRows()
    resetMonthView()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '周计划成果数据加载失败，请稍后重试'
  } finally { loading.value = false }
}

onMounted(loadOptions)
</script>

<style scoped>
.week-result-workspace { --week-result-accent:#2d776c; --week-result-line:#d6dfda; max-width:100%; margin-top:18px; overflow-x:hidden; }
.week-result-loading { margin-top:18px; }
.week-result-board-toolbar,.week-result-month-header,.week-result-toolbar-actions { display:flex; align-items:center; }
.week-result-board-toolbar { justify-content:space-between; gap:18px; min-height:60px; padding:10px 14px; border:1px solid var(--week-result-line); background:#f6f8f5; }
.week-result-board-toolbar > div:first-child { display:grid; min-width:0; gap:3px; }
.week-result-board-toolbar strong { font-size:16px; }
.week-result-board-toolbar span { color:var(--muted); font-size:13px; }
.week-result-toolbar-actions { flex:0 0 auto; gap:9px; }
.week-result-month-list { border:1px solid var(--week-result-line); border-top:0; background:#fff; }
.week-result-month-group + .week-result-month-group { border-top:1px solid var(--week-result-line); }
.week-result-month-header { justify-content:space-between; min-height:58px; padding:7px 12px; background:#f8faf7; }
.week-result-month-toggle { display:grid; grid-template-columns:20px auto 1fr; align-items:center; gap:9px; min-width:0; padding:8px 6px; border:0; color:var(--ink); background:transparent; text-align:left; cursor:pointer; }
.week-result-month-toggle .el-icon { color:var(--week-result-accent); }
.week-result-month-toggle strong { font-size:16px; }
.week-result-month-toggle span { color:var(--muted); font-size:13px; }
.week-result-month-detail { border-top:1px solid var(--week-result-line); }
.week-result-edit-wrap { width:100%; max-width:100%; overflow-x:hidden; }
.week-result-edit-table { width:100%; min-width:0; border-collapse:collapse; table-layout:fixed; color:#263a34; background:#fff; font-size:12px; }
.week-result-edit-table th,.week-result-edit-table td { padding:7px; border-right:1px solid var(--week-result-line); border-bottom:1px solid #dce4df; vertical-align:top; line-height:1.55; overflow-wrap:anywhere; word-break:break-word; }
.week-result-edit-table th { height:44px; color:#3d544d; background:#eef3ef; font-weight:750; text-align:center; vertical-align:middle; }
.week-result-edit-table th:last-child,.week-result-edit-table td:last-child { border-right:0; }
.edit-seq { width:42px; }.edit-work { width:28%; }.edit-completion { width:18%; }.edit-deliverable { width:16%; }.edit-remark { width:12%; }.edit-period { width:106px; }.edit-evidence { width:108px; }.edit-action { width:90px; }
.work-content { white-space:pre-wrap; }
.cell-center { text-align:center; vertical-align:middle!important; }
.week-result-pagination { display:flex; justify-content:flex-end; padding:14px; border-top:1px solid var(--week-result-line); }
.week-result-empty { display:grid; min-height:220px; place-items:center; align-content:center; gap:8px; color:var(--muted); border:1px solid var(--week-result-line); background:#fff; text-align:center; }
.week-result-empty .el-icon { color:var(--week-result-accent); font-size:28px; }
.week-result-empty strong { color:var(--ink); }
.week-result-edit-wrap { border:1px solid var(--week-result-line); border-top:0; }
.week-result-edit-table tr.is-active td { background:#f3f8f5; }
.week-result-edit-table tr.is-readonly td { background:#f7f8f6; }
.week-result-edit-table .sheet-edit-cell { padding:0; background:#fff; }
.week-result-edit-table tr.is-active .sheet-edit-cell { background:#fbfdfb; }
.week-result-edit-table .sheet-edit-cell:focus-within { background:#f3f8f5; box-shadow:inset 0 0 0 1px #7ca397; }
.week-result-edit-table .sheet-edit-cell :deep(.el-textarea),.week-result-edit-table .sheet-edit-cell :deep(.el-textarea__inner) { height:100%; }
.week-result-edit-table .sheet-edit-cell :deep(.el-textarea__inner) { min-height:108px!important; padding:8px; border-radius:0; background:transparent; box-shadow:none; color:#263a34; font-size:12px; line-height:1.55; resize:none; }
.week-result-edit-table .sheet-edit-cell :deep(.el-textarea__inner:focus) { box-shadow:none; }
.week-result-file-picker { display:inline-flex; align-items:center; justify-content:center; gap:5px; min-height:32px; padding:5px 9px; border:1px solid #b9cbc3; border-radius:5px; color:var(--week-result-accent); background:#f5faf7; cursor:pointer; }
.week-result-file-picker:hover { border-color:var(--week-result-accent); background:#edf6f1; }
.week-result-file-picker.is-large { min-width:148px; }
.selected-file-name { display:block; margin-top:6px; color:#536861; overflow-wrap:anywhere; }
.evidence-state.is-uploaded { color:#21705f; font-weight:700; }.muted-text { color:var(--muted); }
.visually-hidden { position:absolute!important; width:1px!important; height:1px!important; overflow:hidden!important; clip:rect(0,0,0,0)!important; white-space:nowrap!important; }
@media (max-width:900px) { .week-result-board-toolbar { align-items:flex-start; flex-direction:column; }.week-result-toolbar-actions { flex-wrap:wrap; width:100%; } }
@media (prefers-reduced-motion:reduce) { .week-result-month-detail { scroll-behavior:auto; } }
</style>
