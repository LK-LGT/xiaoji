<template>
  <section class="task-editor">
    <el-alert v-if="task.status === 'REJECTED'" class="rejection-alert" type="error" :closable="false" show-icon>
      <template #title>周成果已退回，请修改后重新提交</template>
      <p>{{ task.weekResult?.approvalComment || '请根据审批意见完善成果内容。' }}</p>
    </el-alert>

    <header class="task-editor-header">
      <div>
        <div class="task-context">周度执行任务</div>
        <h2>{{ task.taskName }}</h2>
        <p>{{ task.periodLabel }}</p>
      </div>
      <ResultStatusTag :status="task.status" />
    </header>

    <dl class="task-facts">
      <div><dt>计划交付物</dt><dd>{{ task.plannedDeliverable || '未设置' }}</dd></div>
      <div><dt>质量要求</dt><dd>{{ task.quality || '未设置' }}</dd></div>
    </dl>

    <el-form v-if="task.editable" label-position="top" class="result-form">
      <div class="result-form-primary">
        <el-form-item label="完成情况" required :error="errors.completionStatus">
          <el-input v-model="draft.completionStatus" type="textarea" :rows="5" maxlength="1000" show-word-limit placeholder="填写本周实际完成情况" @input="markDirty" />
        </el-form-item>
        <el-form-item label="实际交付物" required :error="errors.deliverable">
          <el-input v-model="draft.deliverable" type="textarea" :rows="5" maxlength="500" show-word-limit placeholder="填写实际形成的交付物" @input="markDirty" />
        </el-form-item>
      </div>
      <el-form-item label="补充说明">
        <el-input v-model="draft.remark" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="补充风险、阻塞或下周安排" @input="markDirty" />
      </el-form-item>

      <div class="evidence-field">
        <div><strong>证明材料</strong><span>支持 {{ acceptedFileTypes.join('、').toUpperCase() }}，不超过 {{ maxFileSizeMb }} MB</span></div>
        <label class="file-picker" :for="`week-result-file-${task.taskId}`"><el-icon><Paperclip /></el-icon>{{ draft.file ? '更换文件' : '选择文件' }}</label>
        <input :id="`week-result-file-${task.taskId}`" class="visually-hidden" type="file" :accept="fileAccept" @change="onFileChange" />
        <div v-if="draft.file" class="selected-file"><span>{{ draft.file.name }}</span><button type="button" aria-label="移除附件" @click="removeFile"><el-icon><Close /></el-icon></button></div>
        <div v-else-if="task.weekResult?.evidences.length" class="existing-evidence">当前版本已上传 {{ task.weekResult.evidences.length }} 个附件。</div>
      </div>
    </el-form>

    <section v-else class="readonly-result">
      <div><strong>完成情况</strong><p>{{ draft.completionStatus || '未填写' }}</p></div>
      <div><strong>实际交付物</strong><p>{{ draft.deliverable || '未填写' }}</p></div>
      <div><strong>补充说明</strong><p>{{ draft.remark || '无' }}</p></div>
    </section>

    <details class="approval-details" :open="task.status === 'REJECTED'">
      <summary>审批进度与意见</summary>
      <div class="approval-line"><span>当前状态</span><strong>{{ statusMeta(task.status).label }}</strong></div>
      <div v-if="task.weekResult?.approvalComment" class="approval-comment"><span>审批意见</span><p>{{ task.weekResult.approvalComment }}</p></div>
    </details>
  </section>
</template>

<script setup lang="ts">
import { Close, Paperclip } from '@element-plus/icons-vue'
import { computed, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { statusMeta, type ResultTaskDraft, type ResultWorkbenchTask } from '../resultWorkbenchModel'
import ResultStatusTag from '@/components/results/ResultStatusTag.vue'

const props = defineProps<{ task: ResultWorkbenchTask; draft: ResultTaskDraft; acceptedFileTypes: string[]; maxFileSizeMb: number }>()
const emit = defineEmits<{ dirty: [] }>()
const errors = reactive({ completionStatus: '', deliverable: '' })
const fileAccept = computed(() => props.acceptedFileTypes.map((type) => `.${type.toLowerCase()}`).join(','))

function markDirty() { props.draft.dirty = true; emit('dirty') }
function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] || null
  if (!file) return
  const extension = file.name.split('.').pop()?.toLowerCase() || ''
  if (!props.acceptedFileTypes.map((item) => item.toLowerCase()).includes(extension)) { ElMessage.warning('文件类型不支持'); input.value = ''; return }
  if (file.size > props.maxFileSizeMb * 1024 * 1024) { ElMessage.warning(`文件大小不能超过 ${props.maxFileSizeMb} MB`); input.value = ''; return }
  props.draft.file = file
  markDirty()
}
function removeFile() { props.draft.file = null; markDirty() }
function validate() {
  errors.completionStatus = props.draft.completionStatus.trim() ? '' : '请填写完成情况'
  errors.deliverable = props.draft.deliverable.trim() ? '' : '请填写实际交付物'
  return !Object.values(errors).some(Boolean)
}
defineExpose({ validate })
</script>

<style scoped>
.task-editor { max-width:920px; margin:0 auto; padding:24px 34px 110px; color:#263a34; }
.rejection-alert { margin-bottom:18px; }.rejection-alert p { margin:6px 0 0; }
.task-editor-header { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; padding-bottom:20px; border-bottom:1px solid #d8e1dc; }
.task-context { color:#2d776c; font-size:12px; font-weight:750; }.task-editor-header h2 { margin:6px 0; font-size:21px; letter-spacing:0; }.task-editor-header p { margin:0; color:#66766f; }
.task-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); margin:0; padding:13px 0; border-bottom:1px solid #d8e1dc; }.task-facts div { display:grid; align-content:start; gap:5px; padding:5px 16px; border-right:1px solid #e2e8e4; }.task-facts div:first-child { padding-left:0; }.task-facts div:last-child { border-right:0; }.task-facts dt { color:#77857f; font-size:11px; }.task-facts dd { margin:0; font-size:13px; line-height:1.5; }
.result-form { padding-top:18px; }.result-form-primary { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }.result-form :deep(.el-form-item) { min-width:0; margin-bottom:18px; }.result-form :deep(.el-form-item__label) { color:#30443d; font-weight:700; }.result-form-primary :deep(.el-textarea__inner) { min-height:138px!important; }
.evidence-field { display:grid; grid-template-columns:1fr auto; gap:10px 18px; padding:18px 0 22px; border-top:1px solid #e0e6e2; }.evidence-field > div:first-child { display:grid; gap:4px; }.evidence-field > div:first-child span { color:#77857f; font-size:12px; }
.file-picker { display:inline-flex; align-items:center; gap:6px; min-height:34px; padding:0 12px; border:1px solid #9eb8ae; border-radius:5px; color:#2d776c; cursor:pointer; }.selected-file,.existing-evidence { grid-column:1/-1; display:flex; align-items:center; justify-content:space-between; min-height:38px; padding:7px 10px; background:#f4f7f5; color:#53675f; font-size:13px; }.selected-file button { display:grid; width:28px; height:28px; place-items:center; border:0; background:transparent; cursor:pointer; }
.readonly-result { display:grid; gap:18px; padding:22px 0; }.readonly-result > div { border-bottom:1px solid #e2e7e4; }.readonly-result p { color:#53665f; line-height:1.7; white-space:pre-wrap; }
.approval-details { margin-top:18px; padding:15px 0; border-top:1px solid #d8e1dc; border-bottom:1px solid #d8e1dc; }.approval-details summary { font-weight:700; cursor:pointer; }.approval-line { display:flex; gap:20px; margin-top:16px; }.approval-comment { margin-top:14px; }.approval-comment span { color:#77857f; font-size:12px; }.approval-comment p { margin:5px 0; line-height:1.65; }
.visually-hidden { position:absolute!important; width:1px!important; height:1px!important; overflow:hidden!important; clip:rect(0,0,0,0)!important; }
@media (max-width:820px) { .result-form-primary { grid-template-columns:1fr; gap:0; } }
@media (max-width:600px) { .task-editor-header { gap:12px; }.task-facts { grid-template-columns:1fr; }.task-facts div { padding:7px 0; border-right:0; border-bottom:1px solid #edf1ee; }.task-facts div:last-child { border-bottom:0; }.evidence-field { grid-template-columns:1fr; }.file-picker { justify-content:center; }.selected-file,.existing-evidence { grid-column:1; } }
</style>
