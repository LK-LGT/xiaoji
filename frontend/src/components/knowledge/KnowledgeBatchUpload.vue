<template>
  <div class="knowledge-batch-upload">
    <div class="preset-toolbar">
      <div class="preset-heading">
        <strong>分类方案</strong>
        <span>保存常用分类，下次可直接套用</span>
      </div>
      <el-select v-model="selectedPresetId" clearable placeholder="选择已保存方案" :disabled="busy" @change="selectPreset">
        <el-option v-for="preset in presets" :key="preset.id" :label="preset.name" :value="preset.id" />
      </el-select>
      <el-input v-model="presetName" maxlength="30" placeholder="方案名称" :disabled="busy" @keyup.enter="savePreset" />
      <el-button :icon="Collection" :disabled="busy" @click="savePreset">保存</el-button>
      <el-tooltip content="删除当前方案" placement="top">
        <el-button
          :icon="Delete"
          :disabled="busy || !selectedPresetId"
          aria-label="删除当前分类方案"
          @click="deletePreset"
        />
      </el-tooltip>
    </div>

    <KnowledgeClassificationForm v-model="defaultMetadata" :orgs="orgs" :disabled="busy" />

    <div
      class="batch-file-picker"
      :class="{ 'is-dragover': dragOver, 'is-compact': items.length }"
      role="button"
      tabindex="0"
      :aria-disabled="busy"
      @click="openFilePicker"
      @keydown.enter.prevent="openFilePicker"
      @keydown.space.prevent="openFilePicker"
      @dragenter.prevent="dragOver = true"
      @dragover.prevent="dragOver = true"
      @dragleave.prevent="dragOver = false"
      @drop.prevent="handleDrop"
    >
      <input
        ref="fileInput"
        class="visually-hidden"
        type="file"
        multiple
        accept=".doc,.docx,.md,.markdown"
        :disabled="busy"
        @change="handleFileInput"
      >
      <el-icon><UploadFilled /></el-icon>
      <div>
        <strong>{{ items.length ? '继续添加文档' : '批量选择文档或拖到这里' }}</strong>
        <span>支持 DOC、DOCX、MD、MARKDOWN，单份不超过 10 MB，单批最多 {{ maxFiles }} 份</span>
      </div>
      <el-button v-if="items.length" :disabled="busy" @click.stop="openFilePicker">选择文件</el-button>
    </div>

    <template v-if="items.length">
      <div class="batch-summary" aria-live="polite">
        <div class="batch-counts">
          <strong>{{ items.length }} 份文档</strong>
          <span>待上传 {{ pendingCount }}</span>
          <span class="is-success">成功 {{ successCount }}</span>
          <span :class="{ 'is-error': failedCount }">失败 {{ failedCount }}</span>
        </div>
        <el-progress :percentage="progress" :stroke-width="7" :show-text="false" :status="failedCount ? 'exception' : successCount === items.length ? 'success' : undefined" />
      </div>

      <div class="batch-actions">
        <el-checkbox
          :model-value="allEditableSelected"
          :indeterminate="someEditableSelected"
          :disabled="busy || !editableItems.length"
          @change="toggleAllEditable"
        >选择待处理文档</el-checkbox>
        <span>已选 {{ selectedEditableIds.length }} 份</span>
        <el-button :disabled="busy || !selectedEditableIds.length" @click="applyDefaultToSelected">套用当前分类</el-button>
        <el-button :disabled="busy || !selectedEditableIds.length" @click="removeSelected">移除所选</el-button>
        <el-button v-if="successCount" :disabled="busy" @click="clearSucceeded">清除成功项</el-button>
      </div>

      <div class="batch-file-list">
        <article v-for="item in items" :key="item.id" class="batch-file" :class="`is-${item.status}`">
          <div class="batch-file__main">
            <el-checkbox
              :model-value="selectedIds.includes(item.id)"
              :disabled="busy || item.status === 'success'"
              :aria-label="`选择 ${item.file.name}`"
              @change="toggleItem(item.id, $event)"
            />
            <el-icon class="file-type-icon"><Document /></el-icon>
            <div class="batch-file__copy">
              <strong :title="item.file.name">{{ item.file.name }}</strong>
              <span>{{ formatFileSize(item.file.size) }} · {{ classificationSummary(item.metadata) }}</span>
              <small v-if="item.error">{{ item.error }}</small>
              <small v-else-if="item.result">{{ item.result.knowledgeId }} · {{ item.result.chunkCount }} 个片段 · {{ item.result.vectorCount }} 条向量</small>
            </div>
            <el-tag :type="statusType(item.status)" effect="plain">{{ statusLabel(item.status) }}</el-tag>
            <div class="batch-file__commands">
              <el-tooltip content="修改该文档分类" placement="top">
                <el-button
                  :icon="EditPen"
                  circle
                  :disabled="busy || item.status === 'success'"
                  :aria-label="`修改 ${item.file.name} 的分类`"
                  @click="toggleEditor(item.id)"
                />
              </el-tooltip>
              <el-tooltip v-if="item.status === 'failed'" content="只重试这份文档" placement="top">
                <el-button :icon="RefreshRight" circle :disabled="busy" :aria-label="`重试 ${item.file.name}`" @click="startUpload([item.id])" />
              </el-tooltip>
              <el-tooltip v-else content="移除文档" placement="top">
                <el-button :icon="CloseBold" circle :disabled="busy || item.status === 'success'" :aria-label="`移除 ${item.file.name}`" @click="removeItem(item.id)" />
              </el-tooltip>
            </div>
          </div>
          <div v-if="expandedIds.includes(item.id)" class="batch-file__editor">
            <KnowledgeClassificationForm v-model="item.metadata" :orgs="orgs" :disabled="busy" />
          </div>
        </article>
      </div>

      <div class="batch-submit-row">
        <span>上传时最多同时处理 {{ concurrency }} 份，失败不会中断其他文档。</span>
        <el-button
          type="primary"
          :icon="UploadFilled"
          :loading="busy"
          :disabled="!uploadableCount"
          @click="startUpload()"
        >{{ busy ? '正在批量入库' : `开始上传 ${uploadableCount} 份` }}</el-button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CloseBold, Collection, Delete, Document, EditPen, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import type { KnowledgeUploadMetadata, KnowledgeUploadResult } from '@/api/knowledge'
import { validateKnowledgeUploadMetadata } from '@/api/knowledge'
import KnowledgeClassificationForm from '@/components/knowledge/KnowledgeClassificationForm.vue'
import {
  appendKnowledgeBatchFiles,
  applyMetadataToBatchItems,
  cloneKnowledgeUploadMetadata,
  emptyKnowledgeUploadMetadata,
  runKnowledgeBatchUpload,
  type KnowledgeBatchItem,
  type KnowledgeBatchStatus,
} from '@/utils/knowledgeBatchUpload'

interface OrgOption {
  id: string | number
  name?: string
  label?: string
  orgType: string
  children?: OrgOption[]
}

interface ClassificationPreset {
  id: string
  name: string
  metadata: KnowledgeUploadMetadata
}

const props = withDefaults(defineProps<{
  orgs: OrgOption[]
  uploadRequest: (file: File, metadata: KnowledgeUploadMetadata) => Promise<KnowledgeUploadResult>
  maxFiles?: number
  concurrency?: number
}>(), { maxFiles: 20, concurrency: 3 })

const emit = defineEmits<{
  (event: 'uploaded', payload: { succeeded: KnowledgeBatchItem[]; failed: KnowledgeBatchItem[] }): void
}>()

const STORAGE_KEY = 'planning_knowledge_upload_presets_v1'
const fileInput = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)
const busy = ref(false)
const items = ref<KnowledgeBatchItem[]>([])
const selectedIds = ref<string[]>([])
const expandedIds = ref<string[]>([])
const defaultMetadata = ref<KnowledgeUploadMetadata>(emptyKnowledgeUploadMetadata())
const presets = ref<ClassificationPreset[]>([])
const selectedPresetId = ref('')
const presetName = ref('')

const editableItems = computed(() => items.value.filter((item) => item.status !== 'success' && item.status !== 'uploading'))
const selectedEditableIds = computed(() => selectedIds.value.filter((id) => editableItems.value.some((item) => item.id === id)))
const allEditableSelected = computed(() => Boolean(editableItems.value.length) && selectedEditableIds.value.length === editableItems.value.length)
const someEditableSelected = computed(() => selectedEditableIds.value.length > 0 && !allEditableSelected.value)
const pendingCount = computed(() => items.value.filter((item) => item.status === 'pending').length)
const successCount = computed(() => items.value.filter((item) => item.status === 'success').length)
const failedCount = computed(() => items.value.filter((item) => item.status === 'failed').length)
const uploadableCount = computed(() => pendingCount.value + failedCount.value)
const progress = computed(() => items.value.length ? Math.round((successCount.value + failedCount.value) / items.value.length * 100) : 0)

function openFilePicker() {
  if (!busy.value) fileInput.value?.click()
}

function handleFileInput(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(Array.from(input.files || []))
  input.value = ''
}

function handleDrop(event: DragEvent) {
  dragOver.value = false
  if (!busy.value) addFiles(Array.from(event.dataTransfer?.files || []))
}

function addFiles(files: File[]) {
  if (!files.length) return
  const appended = appendKnowledgeBatchFiles(items.value, files, defaultMetadata.value, { maxFiles: props.maxFiles })
  items.value = appended.items
  appended.rejected.forEach(({ file, reason }) => ElMessage.warning(`${file.name}：${reason}`))
}

function toggleItem(id: string, checked: string | number | boolean) {
  selectedIds.value = checked ? [...new Set([...selectedIds.value, id])] : selectedIds.value.filter((itemId) => itemId !== id)
}

function toggleAllEditable(checked: string | number | boolean) {
  const editableIds = editableItems.value.map((item) => item.id)
  selectedIds.value = checked
    ? [...new Set([...selectedIds.value, ...editableIds])]
    : selectedIds.value.filter((id) => !editableIds.includes(id))
}

function toggleEditor(id: string) {
  expandedIds.value = expandedIds.value.includes(id)
    ? expandedIds.value.filter((itemId) => itemId !== id)
    : [...expandedIds.value, id]
}

function applyDefaultToSelected() {
  applyMetadataToBatchItems(items.value, selectedEditableIds.value, defaultMetadata.value)
  ElMessage.success(`已为 ${selectedEditableIds.value.length} 份文档套用当前分类`)
}

function removeItem(id: string) {
  items.value = items.value.filter((item) => item.id !== id)
  selectedIds.value = selectedIds.value.filter((itemId) => itemId !== id)
  expandedIds.value = expandedIds.value.filter((itemId) => itemId !== id)
}

function removeSelected() {
  const targets = new Set(selectedEditableIds.value)
  items.value = items.value.filter((item) => !targets.has(item.id))
  selectedIds.value = selectedIds.value.filter((id) => !targets.has(id))
  expandedIds.value = expandedIds.value.filter((id) => !targets.has(id))
}

function clearSucceeded() {
  const succeeded = new Set(items.value.filter((item) => item.status === 'success').map((item) => item.id))
  items.value = items.value.filter((item) => !succeeded.has(item.id))
  selectedIds.value = selectedIds.value.filter((id) => !succeeded.has(id))
  expandedIds.value = expandedIds.value.filter((id) => !succeeded.has(id))
}

async function startUpload(itemIds?: string[]) {
  if (busy.value) return
  busy.value = true
  try {
    const run = await runKnowledgeBatchUpload(items.value, props.uploadRequest, {
      concurrency: props.concurrency,
      itemIds,
      validate: validateKnowledgeUploadMetadata,
    })
    if (!run.started) {
      if (run.invalid.length) {
        const first = run.invalid[0]
        expandedIds.value = [...new Set([...expandedIds.value, first.item.id])]
        ElMessage.warning(`${first.item.file.name}：${first.reason}`)
      } else ElMessage.info('没有待上传或可重试的文档')
      return
    }
    selectedIds.value = selectedIds.value.filter((id) => !run.succeeded.some((item) => item.id === id))
    if (run.failed.length) ElMessage.warning(`已成功 ${run.succeeded.length} 份，失败 ${run.failed.length} 份`)
    else ElMessage.success(`已成功入库 ${run.succeeded.length} 份文档`)
    emit('uploaded', { succeeded: run.succeeded, failed: run.failed })
  } finally {
    busy.value = false
  }
}

function selectPreset(id: string | number | boolean | undefined) {
  const preset = presets.value.find((item) => item.id === String(id || ''))
  if (!preset) return
  defaultMetadata.value = cloneKnowledgeUploadMetadata(preset.metadata)
  presetName.value = preset.name
}

function savePreset() {
  const validation = validateKnowledgeUploadMetadata(defaultMetadata.value)
  if (validation) return ElMessage.warning(validation)
  const name = presetName.value.trim()
  if (!name) return ElMessage.warning('请填写分类方案名称')
  const existing = presets.value.find((item) => item.name === name)
  const preset: ClassificationPreset = {
    id: existing?.id || `${Date.now()}`,
    name,
    metadata: cloneKnowledgeUploadMetadata(defaultMetadata.value),
  }
  presets.value = existing ? presets.value.map((item) => item.id === existing.id ? preset : item) : [...presets.value, preset]
  selectedPresetId.value = preset.id
  persistPresets()
  ElMessage.success(existing ? '分类方案已更新' : '分类方案已保存')
}

function deletePreset() {
  presets.value = presets.value.filter((item) => item.id !== selectedPresetId.value)
  selectedPresetId.value = ''
  presetName.value = ''
  persistPresets()
  ElMessage.success('分类方案已删除')
}

function loadPresets() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    presets.value = Array.isArray(parsed) ? parsed : []
  } catch {
    presets.value = []
  }
}

function persistPresets() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(presets.value))
}

function classificationSummary(metadata: KnowledgeUploadMetadata) {
  const audience = metadata.audienceCodes.map((item) => item === 'MANAGEMENT' ? '管理端' : '员工端').join('、') || '角色未设置'
  const scope = metadata.scopeType === 'GLOBAL' ? '全公司' : metadata.scopeType === 'PROJECT' ? '指定项目' : '指定部门'
  return `${audience} · ${scope} · ${knowledgeTypeLabel(metadata.knowledgeType)}`
}

function knowledgeTypeLabel(value: string) {
  return ({
    HISTORICAL_PLAN: '历史计划', JOB_DESCRIPTION: '岗位说明', DEPARTMENT_GOAL: '部门目标',
    LEADER_REQUIREMENT: '领导要求', PROJECT_MATERIAL: '项目资料', POLICY: '制度规范',
    ACCEPTANCE_STANDARD: '验收标准', REFERENCE_CASE: '参考案例',
  } as Record<string, string>)[value] || '类型未设置'
}

function statusLabel(status: KnowledgeBatchStatus) {
  return { pending: '待上传', uploading: '上传中', success: '已入库', failed: '失败' }[status]
}

function statusType(status: KnowledgeBatchStatus) {
  return ({ pending: 'info', uploading: 'primary', success: 'success', failed: 'danger' } as const)[status]
}

function formatFileSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

onMounted(loadPresets)
</script>

<style scoped>
.knowledge-batch-upload { min-width: 0; }
.preset-toolbar { display: grid; grid-template-columns: minmax(200px, 1.2fr) minmax(180px, 1fr) minmax(160px, .85fr) auto auto; gap: 8px; align-items: center; padding: 12px; background: #f5f7f5; }
.preset-toolbar > * { min-width: 0; }
.preset-toolbar :deep(.el-select), .preset-toolbar :deep(.el-input) { width: 100%; min-width: 0; }
.preset-heading { display: grid; gap: 3px; }
.preset-heading strong { color: var(--ink); font-size: 13px; }
.preset-heading span { color: var(--muted); font-size: 11px; }
.batch-file-picker { display: grid; min-height: 172px; place-content: center; justify-items: center; gap: 10px; padding: 24px; border: 1px dashed #9eb9ad; border-radius: 8px; color: var(--blue); background: #f8faf8; text-align: center; cursor: pointer; transition: border-color 180ms ease, background-color 180ms ease; }
.batch-file-picker:hover, .batch-file-picker.is-dragover { border-color: var(--blue); background: var(--soft-blue); }
.batch-file-picker.is-compact { grid-template-columns: auto minmax(0, 1fr) auto; min-height: 76px; place-content: initial; justify-items: start; text-align: left; }
.batch-file-picker > .el-icon { font-size: 34px; }
.batch-file-picker > div { display: grid; gap: 4px; min-width: 0; }
.batch-file-picker strong { color: var(--ink); font-size: 14px; }
.batch-file-picker span { color: var(--muted); font-size: 11px; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.batch-summary { display: grid; grid-template-columns: minmax(0, 1fr) 180px; gap: 16px; align-items: center; margin-top: 14px; }
.batch-counts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; color: var(--muted); font-size: 11px; }
.batch-counts strong { color: var(--ink); font-size: 13px; }
.batch-counts .is-success { color: #24765a; }
.batch-counts .is-error { color: #b33636; }
.batch-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; padding-block: 10px; border-block: 1px solid var(--line); }
.batch-actions > span { margin-right: auto; color: var(--muted); font-size: 11px; }
.batch-file-list { display: grid; gap: 8px; margin-top: 10px; }
.batch-file { overflow: hidden; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.batch-file.is-failed { border-color: #e8bcbc; }
.batch-file__main { display: grid; grid-template-columns: auto auto minmax(0, 1fr) auto auto; gap: 10px; align-items: center; min-height: 70px; padding: 10px 12px; }
.file-type-icon { color: #2d648b; font-size: 20px; }
.batch-file__copy { display: grid; gap: 3px; min-width: 0; }
.batch-file__copy strong, .batch-file__copy span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.batch-file__copy strong { color: var(--ink); font-size: 12px; }
.batch-file__copy span { color: var(--muted); font-size: 10px; }
.batch-file__copy small { color: #b33636; font-size: 10px; overflow-wrap: anywhere; }
.batch-file.is-success .batch-file__copy small { color: #24765a; }
.batch-file__commands { display: flex; gap: 6px; }
.batch-file__editor { padding: 0 14px 6px 46px; border-top: 1px solid var(--line); background: #fafbfa; }
.batch-file__editor :deep(.classification-form) { margin-bottom: 0; border-top: 0; }
.batch-submit-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-top: 14px; }
.batch-submit-row > span { color: var(--muted); font-size: 11px; }
@media (max-width: 980px) { .preset-toolbar { grid-template-columns: 1fr 1fr auto auto; } .preset-heading { grid-column: 1 / -1; } }
@media (max-width: 720px) { .preset-toolbar { grid-template-columns: 1fr auto auto; } .preset-toolbar > .el-select { grid-column: 1 / -1; } .batch-summary { grid-template-columns: 1fr; } .batch-file__main { grid-template-columns: auto auto minmax(0, 1fr) auto; } .batch-file__main > .el-tag { grid-column: 3; width: fit-content; } .batch-file__commands { grid-column: 4; grid-row: 1 / span 2; } .batch-file__editor { padding-left: 12px; } .batch-submit-row { align-items: stretch; flex-direction: column; } }
@media (prefers-reduced-motion: reduce) { .batch-file-picker { transition: none; } }
</style>
