<template>
  <div class="employee-month-support-actions">
    <el-button plain :icon="Document" @click="openTemplate">模板预览</el-button>
    <el-button v-if="canAdjust" plain type="warning" :icon="Setting" @click="adjustVisible = true">申请暂停或撤销</el-button>
  </div>

  <el-drawer v-model="templateVisible" title="月计划模板预览" size="min(960px, 96vw)">
    <div class="template-actions">
      <el-button :loading="templateLoading" @click="loadTemplate">刷新预览</el-button>
      <el-button type="primary" :loading="templateDownloading" @click="downloadTemplate">下载 Excel</el-button>
      <el-button :icon="Printer" :disabled="templateLoading || !template" @click="printTemplate">打印</el-button>
    </div>
    <el-skeleton v-if="templateLoading" :rows="8" animated />
    <div v-else-if="template" class="template-preview">
      <h2>{{ planMonthTitle(template.planMonth) }}</h2>
      <div class="template-preview__meta">
        <span>姓名：{{ template.employeeName || detail.employeeName }}</span>
        <span>项目：{{ template.projectName || '' }}</span>
        <span>岗位：{{ template.positionName || detail.positionName || '' }}</span>
      </div>
      <div class="template-preview__table-wrap">
        <table>
          <thead>
            <tr>
              <th>序号</th>
              <th>工作计划内容</th>
              <th>权重（%）</th>
              <th>考核标准（交付物、质量）</th>
              <th>完成时间段</th>
              <th>预估价值</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in templateRows" :key="row.rowNo">
              <td>{{ row.rowNo }}</td>
              <td>
                <MonthPlanTaskContentView :content="row.taskContent" empty-text="" />
              </td>
              <td>{{ row.performanceWeight || '' }}</td>
              <td class="template-standard-cell">{{ formatMonthPlanStandard(row.deliverable, row.quality, '') }}</td>
              <td>{{ formatMonthPlanPeriod(row.estimatedStartDate, row.estimatedEndDate, '') }}</td>
              <td>{{ row.estimatedValue ? formatCurrency(row.estimatedValue) : '' }}</td>
            </tr>
            <tr>
              <td colspan="5">预估价值合计</td>
              <td>{{ template.estimatedValueTotal ? formatCurrency(template.estimatedValueTotal) : '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <el-empty v-else description="暂无模板预览" />
  </el-drawer>

  <el-dialog v-model="adjustVisible" title="申请暂停或撤销月计划" width="min(560px, calc(100vw - 32px))">
    <el-form label-position="top">
      <el-form-item label="处理类型">
        <el-radio-group v-model="adjustForm.adjustmentType">
          <el-radio-button value="PAUSE">暂停</el-radio-button>
          <el-radio-button value="CANCEL">撤销</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="申请原因">
        <el-input v-model="adjustForm.reason" type="textarea" :rows="4" maxlength="1000" show-word-limit />
      </el-form-item>
      <el-form-item label="影响说明">
        <el-input v-model="adjustForm.impactText" type="textarea" :rows="3" maxlength="1000" show-word-limit />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="adjustVisible = false">取消</el-button>
      <el-button type="primary" :loading="adjustSubmitting" @click="submitAdjustment">提交申请</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Document, Printer, Setting } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  createEmployeePlanAdjustmentApi,
  downloadEmployeeMonthPlanTemplateApi,
  getEmployeeMonthPlanTemplatePreviewApi,
  type EmployeeMonthPlanDetailResp,
  type EmployeeMonthPlanTemplatePreviewItem,
  type EmployeeMonthPlanTemplatePreviewResp,
} from '@/api/employee'
import { saveBlob } from '@/utils/download'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'

const props = defineProps<{ detail: EmployeeMonthPlanDetailResp }>()
const templateVisible = ref(false)
const templateLoading = ref(false)
const templateDownloading = ref(false)
const template = ref<EmployeeMonthPlanTemplatePreviewResp | null>(null)
const adjustVisible = ref(false)
const adjustSubmitting = ref(false)
const adjustForm = reactive({
  adjustmentType: 'PAUSE' as 'PAUSE' | 'CANCEL',
  reason: '',
  impactText: '',
})

const canAdjust = computed(() => props.detail.status === 'submitted' || props.detail.status === 'approved')
const templateRows = computed<EmployeeMonthPlanTemplatePreviewItem[]>(() => {
  if (template.value?.items.length) return template.value.items
  return Array.from({ length: 8 }, (_, index) => ({
    rowNo: index + 1,
    taskContent: '',
    performanceWeight: 0,
    deliverable: '',
    quality: '',
    estimatedStartDate: '',
    estimatedEndDate: '',
    estimatedValue: 0,
  }))
})

async function openTemplate() {
  templateVisible.value = true
  await loadTemplate()
}

async function loadTemplate() {
  templateLoading.value = true
  try {
    template.value = await getEmployeeMonthPlanTemplatePreviewApi(props.detail.id)
  } catch (error) {
    template.value = null
    ElMessage.error(error instanceof Error ? error.message : '模板预览加载失败')
  } finally {
    templateLoading.value = false
  }
}

async function downloadTemplate() {
  templateDownloading.value = true
  try {
    const blob = await downloadEmployeeMonthPlanTemplateApi(props.detail.id)
    saveBlob(blob, `${props.detail.employeeName || '员工'}-${props.detail.planMonth}-月计划.xlsx`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '模板下载失败')
  } finally {
    templateDownloading.value = false
  }
}

function printTemplate() {
  if (!template.value) {
    ElMessage.warning('请先加载模板预览')
    return
  }
  const printWindow = window.open('', '_blank', 'width=1120,height=780')
  if (!printWindow) {
    ElMessage.error('浏览器拦截了打印窗口，请允许弹窗后重试')
    return
  }
  printWindow.document.open()
  printWindow.document.write(buildTemplatePrintHtml())
  printWindow.document.close()
  printWindow.focus()
  window.setTimeout(() => {
    printWindow.print()
  }, 180)
}

async function submitAdjustment() {
  if (!adjustForm.reason.trim()) {
    ElMessage.warning('请填写申请原因')
    return
  }
  adjustSubmitting.value = true
  try {
    await createEmployeePlanAdjustmentApi({
      planType: 'MONTH',
      planId: props.detail.id,
      adjustmentType: adjustForm.adjustmentType,
      reason: adjustForm.reason.trim(),
      impactText: adjustForm.impactText.trim(),
    })
    adjustVisible.value = false
    adjustForm.reason = ''
    adjustForm.impactText = ''
    ElMessage.success('调整申请已提交直属领导处理')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '调整申请提交失败')
  } finally {
    adjustSubmitting.value = false
  }
}

function planMonthTitle(planMonth: string) {
  const [year, month] = String(planMonth || '').split('-')
  const monthNumber = Number(month)
  if (!year || !Number.isFinite(monthNumber)) return '        年      月个人目标制定'
  return `${year} 年 ${String(monthNumber).padStart(2, '0')} 月个人目标制定`
}

function formatCurrency(value: number) {
  return `¥${Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function formatPlainNumber(value?: number | null) {
  const numericValue = Number(value || 0)
  if (!Number.isFinite(numericValue)) return ''
  return numericValue.toLocaleString('zh-CN', { maximumFractionDigits: 2, useGrouping: false })
}

function buildTemplatePrintHtml() {
  const current = template.value
  if (!current) return ''
  const title = escapeHtml(planMonthTitle(current.planMonth))
  const rows = templateRows.value.map((row) => `
    <tr class="template-print-item-row">
      <td class="cell-center">${escapeHtml(row.rowNo)}</td>
      <td>${renderTaskContent(row.taskContent)}</td>
      <td class="cell-center">${row.performanceWeight || ''}</td>
      <td>${renderStandard(row.deliverable, row.quality)}</td>
      <td class="cell-center">${escapeHtml(formatMonthPlanPeriod(row.estimatedStartDate, row.estimatedEndDate, ''))}</td>
      <td class="cell-center">${row.estimatedValue ? escapeHtml(formatPlainNumber(row.estimatedValue)) : ''}</td>
    </tr>
  `).join('')
  const total = current.estimatedValueTotal ? escapeHtml(formatPlainNumber(current.estimatedValueTotal)) : ''
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>${title}</title>
  <style>
    @page { size: A4 landscape; margin: 0; }
    * { box-sizing: border-box; }
    html, body { width: 297mm; min-height: 210mm; margin: 0; padding: 0; background: #fff; }
    body {
      color: #000;
      font-family: "SimSun", "Microsoft YaHei", Arial, sans-serif;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .sheet { width: 297mm; min-height: 210mm; margin: 0 auto; padding: 9mm 12mm; background: #fff; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; color: #000; font-size: 8.5px; line-height: 1.32; }
    col:nth-child(1) { width: 5.5%; }
    col:nth-child(2) { width: 29.4%; }
    col:nth-child(3) { width: 4.9%; }
    col:nth-child(4) { width: 35.2%; }
    col:nth-child(5) { width: 13.8%; }
    col:nth-child(6) { width: 11.2%; }
    th, td { padding: 3px 4px; border: 1px solid #000; vertical-align: middle; word-break: break-word; overflow-wrap: anywhere; }
    th { background: #fff; font-weight: 700; text-align: center; }
    .template-print-title-row td {
      height: 25px;
      padding: 0 4px 4px;
      border: 0;
      font-size: 18px;
      font-weight: 700;
      line-height: 1.2;
      text-align: center;
    }
    .template-print-meta-row td {
      height: 24px;
      padding: 0 0 5px;
      border: 0;
      font-size: 10px;
      line-height: 1.2;
    }
    .template-print-meta {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 28px;
      width: 100%;
      white-space: nowrap;
    }
    .template-print-column-row th { height: 30px; }
    .template-print-item-row td { vertical-align: top; }
    .template-print-item-row td.cell-center, .cell-center { text-align: center; vertical-align: middle !important; }
    .task-title, .standard-label { font-weight: 700; }
    .task-title { display: block; margin-bottom: 2px; }
    .line { display: block; }
    .template-print-total-row td { height: 19px; font-weight: 700; text-align: center; vertical-align: middle; }
    .template-print-confirm-row td {
      height: 62px;
      padding: 7px 8px;
      font-size: 10px;
      line-height: 1.6;
      vertical-align: top;
    }
    .template-print-confirm-row p { margin: 0 0 7px; }
    .template-print-signature-line {
      display: flex;
      align-items: end;
      margin-top: 3px;
      white-space: nowrap;
    }
    .template-print-signature-label { width: 120px; }
    .template-print-signature-blank { display: inline-block; width: 210px; min-height: 1em; border-bottom: 1px solid #000; }
    .template-print-signature-date { margin-left: 285px; }
    tr { break-inside: avoid; page-break-inside: avoid; }
  </style>
</head>
<body>
  <main class="sheet">
    <table>
      <colgroup><col><col><col><col><col><col></colgroup>
      <tbody>
        <tr class="template-print-title-row">
          <td colspan="6">${title}</td>
        </tr>
        <tr class="template-print-meta-row">
          <td colspan="6">
            <div class="template-print-meta">
              <span>姓名：${escapeHtml(current.employeeName || props.detail.employeeName || '')}</span>
              <span>项目：${escapeHtml(current.projectName || '')}</span>
              <span>岗位：${escapeHtml(current.positionName || props.detail.positionName || '')}</span>
            </div>
          </td>
        </tr>
        <tr class="template-print-column-row">
          <th>序号</th>
          <th>工作计划内容</th>
          <th>权重（%）</th>
          <th>考核标准（交付物、质量）</th>
          <th>完成时间段（从开始到结束时间）</th>
          <th>预估价值</th>
        </tr>
        ${rows}
        <tr class="template-print-total-row">
          <td colspan="5">预估价值合计</td>
          <td>${total}</td>
        </tr>
        <tr class="template-print-confirm-row">
          <td colspan="6">
            <p>本人核对表格内月度工作计划目标，确认信息真实完整无误，知晓并认可表格兼具目标确定和后续绩效考核有关，对以上内容无异议。</p>
            <div class="template-print-signature-line"><span class="template-print-signature-label">员工本人签字捺印：</span><span class="template-print-signature-blank"></span><span class="template-print-signature-date">时间：____年__月__日</span></div>
            <div class="template-print-signature-line"><span class="template-print-signature-label">项目经理签字：</span><span class="template-print-signature-blank"></span><span class="template-print-signature-date">时间：____年__月__日</span></div>
            <div class="template-print-signature-line"><span class="template-print-signature-label">部门负责人签字：</span><span class="template-print-signature-blank"></span><span class="template-print-signature-date">时间：____年__月__日</span></div>
          </td>
        </tr>
      </tbody>
    </table>
  </main>
</body>
</html>`
}

function renderTaskContent(value?: string | null) {
  const lines = linesOf(value)
  if (!lines.length) return ''
  if (lines.length === 1) return escapeHtml(lines[0])
  const [title, ...rest] = lines
  return `<span class="task-title">${escapeHtml(title)}</span>${rest.map((line) => `<span class="line">${escapeHtml(line)}</span>`).join('')}`
}

function renderStandard(deliverable?: string | null, quality?: string | null) {
  const deliverableText = String(deliverable || '').trim()
  const qualityText = String(quality || '').trim()
  return [
    deliverableText ? `<span class="standard-label">交付物：</span>${escapeHtml(deliverableText)}` : '',
    qualityText ? `<span class="standard-label">质量：</span>${escapeHtml(qualityText)}` : '',
  ].filter(Boolean).map((line) => `<span class="line">${line}</span>`).join('')
}

function linesOf(value?: string | null) {
  return String(value || '').replace(/\r\n?/g, '\n').split('\n').map((line) => line.trim()).filter(Boolean)
}

function escapeHtml(value: unknown) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
</script>

<style scoped>
.employee-month-support-actions { display: contents; }
.employee-month-support-actions .el-button { margin-left: 0; }
.template-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.template-preview { color: #243a33; }
.template-preview h2 { margin: 0 0 12px; font-size: 20px; text-align: center; }
.template-preview__meta { display: flex; flex-wrap: wrap; gap: 8px 24px; margin-bottom: 12px; color: #52665f; font-size: 13px; }
.template-preview__table-wrap { overflow-x: auto; }
.template-preview table { width: 100%; min-width: 840px; border-collapse: collapse; font-size: 13px; }
.template-preview th,
.template-preview td { padding: 9px 10px; border: 1px solid #cfdad4; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
.template-preview th { color: #30473f; background: #f1f5f2; font-weight: 750; }
.template-standard-cell { white-space: pre-wrap; }
</style>
