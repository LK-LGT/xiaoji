<template>
  <div class="result-submission">
    <!-- 成果头部 -->
    <el-card class="result-header">
      <template #header>
        <div class="card-header">
          <span>成果提交</span>
          <el-tag :type="statusTag">{{ statusLabel }}</el-tag>
        </div>
      </template>

      <div class="header-info">
        <div class="info-item">
          <span class="label">关联计划</span>
          <span class="value">{{ planTitle }}</span>
        </div>
        <div class="info-item">
          <span class="label">计划周期</span>
          <span class="value">{{ periodLabel }}</span>
        </div>
        <div class="info-item">
          <span class="label">完成情况</span>
          <el-progress :percentage="completionRate" />
        </div>
      </div>
    </el-card>

    <!-- 成果内容 -->
    <el-card class="result-content">
      <template #header>
        <span>成果描述</span>
      </template>

      <div v-if="!isEditing" class="result-view">
        <div v-if="result.description" class="description">
          {{ result.description }}
        </div>
        <div v-else class="empty-state">
          <p>暂无成果描述</p>
        </div>
      </div>

      <div v-else class="result-edit">
        <el-input
          v-model="result.description"
          type="textarea"
          rows="6"
          placeholder="请输入成果描述"
          show-word-limit
          maxlength="2000"
        />
      </div>

      <div v-if="!isEditing && result.description" class="meta">
        <span>最后修改: {{ lastModified }}</span>
      </div>
    </el-card>

    <!-- 证据上传 -->
    <el-card class="evidence-section">
      <template #header>
        <div class="card-header">
          <span>成果证据</span>
          <span v-if="result.evidences.length" class="count-badge">{{ result.evidences.length }} 个</span>
        </div>
      </template>

      <div class="evidence-list">
        <div v-for="(evidence, idx) in result.evidences" :key="idx" class="evidence-item">
          <div class="evidence-icon">
            <span>📎</span>
          </div>
          <div class="evidence-info">
            <div class="evidence-name">{{ evidence.name }}</div>
            <div class="evidence-meta">
              {{ formatFileSize(evidence.size) }} • {{ evidence.uploadDate }}
            </div>
          </div>
          <div class="evidence-actions" v-if="isEditing">
            <el-button link type="danger" size="small" @click="removeEvidence(idx)">
              删除
            </el-button>
          </div>
        </div>

        <div v-if="!result.evidences.length" class="empty-state">
          <p>暂无证据文件</p>
        </div>
      </div>

      <div v-if="isEditing" class="upload-area">
        <el-upload
          drag
          multiple
          action="#"
          :auto-upload="false"
          :on-change="handleEvidenceUpload"
          accept="image/*,.pdf,.doc,.docx,.xls,.xlsx"
        >
          <el-icon class="el-icon--upload"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 4v12m0 0l-4-4m4 4l4-4M4 19h16" /></svg></el-icon>
          <div class="el-upload__text">
            拖拽文件到此或<em>点击选择</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              支持 JPG, PNG, PDF, Word, Excel 等格式，单个文件不超过 100MB
            </div>
          </template>
        </el-upload>
      </div>
    </el-card>

    <!-- 自查和检查结果 -->
    <el-card class="inspection-section">
      <template #header>
        <span>质量检查</span>
      </template>

      <div class="inspection-items">
        <div class="inspection-item">
          <span class="item-label">内容笼统</span>
          <el-tag :type="result.issues.isVague ? 'danger' : 'success'">
            {{ result.issues.isVague ? '需改进' : '通过' }}
          </el-tag>
        </div>
        <div class="inspection-item">
          <span class="item-label">交付物可核验</span>
          <el-tag :type="result.issues.deliverableNotVerifiable ? 'danger' : 'success'">
            {{ result.issues.deliverableNotVerifiable ? '需改进' : '通过' }}
          </el-tag>
        </div>
        <div class="inspection-item">
          <span class="item-label">任务冲突</span>
          <el-tag :type="result.issues.hasConflict ? 'danger' : 'success'">
            {{ result.issues.hasConflict ? '需改进' : '通过' }}
          </el-tag>
        </div>
        <div class="inspection-item">
          <span class="item-label">证据充分</span>
          <el-tag :type="result.issues.insufficientEvidence ? 'danger' : 'success'">
            {{ result.issues.insufficientEvidence ? '需改进' : '通过' }}
          </el-tag>
        </div>
      </div>

      <div class="ai-check-section">
        <el-button @click="runAiCheck" :loading="aiChecking">
          🤖 运行 AI 检查
        </el-button>
      </div>
    </el-card>

    <!-- 审批历史 -->
    <el-card v-if="approvalHistory.length" class="history-section">
      <template #header>
        <span>审批历史</span>
      </template>

      <el-timeline>
        <el-timeline-item
          v-for="(record, idx) in approvalHistory"
          :key="idx"
          :timestamp="record.timestamp"
          placement="top"
          :type="record.type"
        >
          <p>
            <strong>{{ record.action }}</strong> by {{ record.approverName }}
          </p>
          <p v-if="record.comment" class="comment">{{ record.comment }}</p>
        </el-timeline-item>
      </el-timeline>
    </el-card>

    <!-- 操作按钮 -->
    <div class="action-footer">
      <div class="left-actions">
        <el-button @click="onCancel">返回</el-button>
      </div>

      <div class="right-actions">
        <el-button v-if="!isEditing && canEdit" @click="startEdit">
          编辑
        </el-button>
        <el-button v-if="isEditing" @click="cancelEdit">
          取消编辑
        </el-button>
        <el-button v-if="isEditing" type="primary" @click="saveDraft" :loading="saving">
          保存草稿
        </el-button>
        <el-button v-if="canSubmit" type="primary" @click="submitResult" :loading="saving">
          提交成果
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'

interface Evidence {
  name: string
  size: number
  uploadDate: string
  url?: string
}

interface Result {
  id?: string
  planId: string
  description: string
  evidences: Evidence[]
  issues: {
    isVague: boolean
    deliverableNotVerifiable: boolean
    hasConflict: boolean
    insufficientEvidence: boolean
  }
}

interface ApprovalRecord {
  action: string
  approverName: string
  timestamp: string
  type: 'success' | 'warning' | 'danger'
  comment?: string
}

const props = withDefaults(
  defineProps<{
    planTitle?: string
    periodLabel?: string
    status?: 'draft' | 'submitted' | 'approved' | 'rejected'
    canEdit?: boolean
    canSubmit?: boolean
    initialData?: Partial<Result>
    approvalHistory?: ApprovalRecord[]
  }>(),
  {
    planTitle: '未指定',
    periodLabel: '2026-11-01 至 2026-11-30',
    status: 'draft',
    canEdit: true,
    canSubmit: true,
    approvalHistory: () => [],
  }
)

const emit = defineEmits<{
  save: [data: Result]
  submit: [data: Result]
  cancel: []
}>()

const isEditing = ref(false)
const saving = ref(false)
const aiChecking = ref(false)
const completionRate = ref(0)

const result = reactive<Result>({
  id: '',
  planId: '',
  description: '',
  evidences: [],
  issues: {
    isVague: false,
    deliverableNotVerifiable: false,
    hasConflict: false,
    insufficientEvidence: false,
  },
  ...props.initialData,
})

const statusLabel = computed(() => {
  const map: Record<string, string> = {
    draft: '草稿',
    submitted: '已提交',
    approved: '已批准',
    rejected: '已驳回',
  }
  return map[props.status] || '未知'
})

const statusTag = computed(() => {
  const map: Record<string, string> = {
    draft: 'info',
    submitted: 'warning',
    approved: 'success',
    rejected: 'danger',
  }
  return map[props.status] || 'info'
})

const lastModified = computed(() => {
  const now = new Date()
  return now.toLocaleString('zh-CN')
})

const approvalHistory = computed(() => props.approvalHistory)

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i]
}

function handleEvidenceUpload(file: any) {
  if (file.raw) {
    result.evidences.push({
      name: file.name,
      size: file.size,
      uploadDate: new Date().toLocaleDateString('zh-CN'),
      url: URL.createObjectURL(file.raw),
    })
  }
}

function removeEvidence(index: number) {
  result.evidences.splice(index, 1)
}

function startEdit() {
  isEditing.value = true
}

function cancelEdit() {
  isEditing.value = false
}

async function saveDraft() {
  saving.value = true
  try {
    emit('save', { ...result })
    ElMessage.success('草稿已保存')
  } finally {
    saving.value = false
  }
}

async function submitResult() {
  if (!result.description.trim()) {
    ElMessage.warning('请输入成果描述')
    return
  }

  if (!result.evidences.length) {
    ElMessage.warning('请至少上传一个证据文件')
    return
  }

  saving.value = true
  try {
    emit('submit', { ...result })
    ElMessage.success('成果已提交')
    isEditing.value = false
  } finally {
    saving.value = false
  }
}

async function runAiCheck() {
  aiChecking.value = true
  try {
    // 模拟 AI 检查
    await new Promise((resolve) => setTimeout(resolve, 2000))

    // 更新检查结果
    result.issues.isVague = Math.random() > 0.7
    result.issues.deliverableNotVerifiable = Math.random() > 0.8
    result.issues.hasConflict = false
    result.issues.insufficientEvidence = result.evidences.length === 0

    ElMessage.success('AI 检查完成')
  } finally {
    aiChecking.value = false
  }
}

function onCancel() {
  emit('cancel')
}
</script>

<style scoped lang="scss">
.result-submission {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.header-info {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;

  .info-item {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;

    .label {
      font-size: 0.875rem;
      color: var(--el-text-color-regular);
      font-weight: 500;
    }

    .value {
      font-size: 1rem;
      color: var(--el-text-color-primary);
    }
  }
}

.result-view,
.result-edit {
  min-height: 120px;

  .description {
    font-size: 1rem;
    line-height: 1.6;
    color: var(--el-text-color-primary);
    white-space: pre-wrap;
  }
}

.meta {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--el-border-color);
  font-size: 0.875rem;
  color: var(--el-text-color-regular);
}

.evidence-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;

  .evidence-item {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem;
    background: var(--el-fill-color-light);
    border-radius: 0.5rem;

    .evidence-icon {
      font-size: 1.5rem;
    }

    .evidence-info {
      flex: 1;

      .evidence-name {
        font-weight: 500;
        color: var(--el-text-color-primary);
      }

      .evidence-meta {
        font-size: 0.875rem;
        color: var(--el-text-color-regular);
      }
    }

    .evidence-actions {
      display: flex;
      gap: 0.5rem;
    }
  }
}

.upload-area {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--el-border-color);
}

.empty-state {
  padding: 2rem 1rem;
  text-align: center;
  color: var(--el-text-color-placeholder);
}

.inspection-items {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;

  .inspection-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1rem;
    background: var(--el-fill-color-light);
    border-radius: 0.5rem;

    .item-label {
      font-weight: 500;
      color: var(--el-text-color-primary);
    }
  }
}

.ai-check-section {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--el-border-color);
  text-align: center;
}

.history-section {
  margin-top: 1rem;
}

.action-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.5rem 0;
  border-top: 1px solid var(--el-border-color);

  .left-actions,
  .right-actions {
    display: flex;
    gap: 0.75rem;
  }

  .right-actions {
    justify-content: flex-end;
  }
}
</style>
