<template>
  <div class="plan-example-input">
    <input
      ref="fileInput"
      class="visually-hidden"
      type="file"
      accept=".txt,.md,.markdown,.doc,.docx,.pdf,.xls,.xlsx"
      :disabled="disabled || extracting"
      @change="selectFile"
    />

    <div class="example-actions">
      <el-button :icon="Paperclip" :loading="extracting" :disabled="disabled" @click="openFilePicker">
        {{ extracting ? '正在读取' : '上传示例' }}
      </el-button>
      <el-popover v-model:visible="pasteVisible" placement="top-start" :width="420" trigger="click">
        <template #reference>
          <el-button :icon="EditPen" :disabled="disabled || extracting">粘贴示例</el-button>
        </template>
        <div class="paste-panel">
          <strong>粘贴计划示例</strong>
          <span>AI 只参考结构、任务颗粒度和表达方式。</span>
          <el-input
            v-model="pasteContent"
            type="textarea"
            :autosize="{ minRows: 6, maxRows: 12 }"
            maxlength="20000"
            show-word-limit
            placeholder="粘贴示例计划内容"
          />
          <div class="paste-panel__actions">
            <el-button @click="pasteVisible = false">取消</el-button>
            <el-button type="primary" :disabled="!pasteContent.trim()" @click="applyPaste">使用此示例</el-button>
          </div>
        </div>
      </el-popover>
    </div>

    <div v-if="modelValue?.content" class="example-file" aria-live="polite">
      <el-icon><DocumentChecked /></el-icon>
      <div>
        <strong>{{ modelValue.fileName || '粘贴内容' }}</strong>
        <span>已读取 {{ modelValue.content.length }} 字{{ modelValue.truncated ? '，超长部分已省略' : '' }}</span>
      </div>
      <el-tooltip content="移除示例" placement="top">
        <el-button :icon="Delete" text circle aria-label="移除示例" :disabled="disabled" @click="clearExample" />
      </el-tooltip>
    </div>
    <span v-else class="example-note">支持 Word、PDF、Excel、Markdown 和文本，仅本次生成使用</span>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, DocumentChecked, EditPen, Paperclip } from '@element-plus/icons-vue'
import { extractPlanExampleApi, type AiGenerationExample } from '@/api/employeeAi'

const props = defineProps<{ modelValue?: AiGenerationExample; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: AiGenerationExample | undefined] }>()

const fileInput = ref<HTMLInputElement>()
const extracting = ref(false)
const pasteVisible = ref(false)
const pasteContent = ref('')

watch(() => props.modelValue, (value) => {
  if (value?.fileName === '粘贴内容') pasteContent.value = value.content
})

function openFilePicker() {
  if (!props.disabled && !extracting.value) fileInput.value?.click()
}

async function selectFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > 10 * 1024 * 1024) {
    ElMessage.warning('示例文件不能超过 10MB')
    return
  }
  extracting.value = true
  try {
    const result = await extractPlanExampleApi(file)
    pasteContent.value = ''
    emit('update:modelValue', result)
    ElMessage.success('示例已读取，将用于本次计划生成')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '示例文件读取失败')
  } finally {
    extracting.value = false
  }
}

function applyPaste() {
  const content = pasteContent.value.trim()
  if (!content) return
  emit('update:modelValue', { fileName: '粘贴内容', content })
  pasteVisible.value = false
  ElMessage.success('粘贴内容将作为本次生成示例')
}

function clearExample() {
  emit('update:modelValue', undefined)
  pasteContent.value = ''
}
</script>

<style scoped>
.plan-example-input { display:flex; min-width:0; align-items:center; flex-wrap:wrap; gap:8px 10px; }
.visually-hidden { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); clip-path:inset(50%); white-space:nowrap; }
.example-actions { display:flex; align-items:center; gap:8px; }
.example-actions :deep(.el-button) { margin:0; }
.example-file { display:grid; grid-template-columns:20px minmax(0,1fr) 28px; min-width:220px; max-width:420px; align-items:center; gap:8px; padding:6px 7px 6px 9px; border:1px solid #cfe0d8; border-radius:7px; color:#24584f; background:#f4f9f6; }
.example-file > .el-icon { font-size:18px; }
.example-file > div { display:grid; min-width:0; gap:1px; }
.example-file strong,.example-file span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.example-file strong { color:#28463f; font-size:12px; }
.example-file span,.example-note { color:#6b7c76; font-size:11px; }
.paste-panel { display:grid; gap:10px; }
.paste-panel > strong { color:#263d36; font-size:14px; }
.paste-panel > span { color:#667873; font-size:12px; }
.paste-panel__actions { display:flex; justify-content:flex-end; gap:8px; }
@media (max-width:680px) {
  .plan-example-input,.example-actions { align-items:stretch; width:100%; }
  .example-actions > :deep(.el-button),.example-actions > :deep(.el-popover__reference-wrapper) { flex:1; }
  .example-actions :deep(.el-button) { width:100%; }
  .example-file { width:100%; max-width:none; }
  .example-note { width:100%; }
}
</style>
