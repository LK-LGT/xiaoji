<template>
  <div class="plan-editor">
    <!-- 计划头部信息 -->
    <el-card class="plan-header-card">
      <template #header>
        <div class="card-header">
          <span class="title">
            {{ isNew ? '新建' : '编辑' }}
            {{ typeLabel }} - {{ modeLabel }}
          </span>
          <div class="actions">
            <el-tag v-if="!isNew" :type="statusTag">{{ statusLabel }}</el-tag>
            <el-button v-if="canEdit" link type="primary" @click="toggleEdit">
              {{ isEditing ? '取消编辑' : '编辑' }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="header-info">
        <div class="info-row">
          <div class="info-item">
            <span class="label">计划周期</span>
            <span class="value">
              {{ form.periodStart }} ~ {{ form.periodEnd }}
            </span>
          </div>
          <div class="info-item">
            <span class="label">计划类型</span>
            <span class="value">{{ typeLabel }}</span>
          </div>
          <div class="info-item">
            <span class="label">权重</span>
            <el-input-number
              v-if="isEditing"
              v-model="form.weight"
              :min="0"
              :max="100"
              :precision="1"
              @change="onFormChange"
            />
            <span v-else class="value">{{ form.weight }}%</span>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 计划内容编辑 -->
    <el-card class="plan-content-card">
      <template #header>
        <div class="card-header">
          <span>计划内容</span>
          <span v-if="form.items.length" class="count-badge">{{ form.items.length }} 项</span>
        </div>
      </template>

      <div class="plan-items">
        <div v-for="(item, index) in form.items" :key="item.id || index" class="plan-item">
          <div class="item-header">
            <span class="item-number">#{{ index + 1 }}</span>
            <el-input
              v-if="isEditing"
              v-model="item.title"
              placeholder="请输入计划事项标题"
              @input="onFormChange"
              class="item-title-input"
            />
            <span v-else class="item-title">{{ item.title }}</span>

            <div class="item-actions" v-if="isEditing">
              <el-button link type="primary" size="small" @click="editItem(item)">
                详情
              </el-button>
              <el-button link type="danger" size="small" @click="removeItem(index)">
                删除
              </el-button>
            </div>
          </div>

          <div v-if="item.description" class="item-description">
            {{ item.description }}
          </div>

          <div class="item-meta">
            <span v-if="item.deliverable" class="meta-badge">
              📦 交付物: {{ item.deliverable }}
            </span>
            <span v-if="item.dueDate" class="meta-badge">
              📅 截止: {{ item.dueDate }}
            </span>
            <span v-if="item.priority" class="meta-badge" :class="`priority-${item.priority}`">
              🎯 优先级: {{ priorityLabel(item.priority) }}
            </span>
          </div>
        </div>

        <div v-if="!form.items.length" class="empty-state">
          <p>暂无计划事项</p>
        </div>

        <div v-if="isEditing" class="add-item-action">
          <el-button @click="addItem" type="primary" link>+ 添加计划事项</el-button>
        </div>
      </div>
    </el-card>

    <!-- 验收标准 -->
    <el-card v-if="type !== 'DAY'" class="acceptance-card">
      <template #header>
        <div class="card-header">
          <span>验收标准</span>
          <el-button v-if="isEditing" link type="primary" size="small" @click="showAcceptanceDialog = true">
            配置
          </el-button>
        </div>
      </template>

      <div v-if="form.acceptanceCriteria.length" class="criteria-list">
        <div v-for="(criterion, idx) in form.acceptanceCriteria" :key="idx" class="criterion">
          <span class="criterion-label">{{ criterion.label }}</span>
          <span class="criterion-description">{{ criterion.description }}</span>
        </div>
      </div>
      <div v-else class="empty-state">
        <p>暂无验收标准</p>
      </div>
    </el-card>

    <!-- 行动按钮 -->
    <div class="action-footer">
      <div class="left-actions">
        <el-button @click="onCancel">取消</el-button>
      </div>

      <div class="right-actions">
        <el-button v-if="isEditing && canEdit" @click="saveDraft" :loading="saving">
          保存草稿
        </el-button>
        <el-button v-if="isEditing && canSubmit" type="primary" @click="submitPlan" :loading="saving">
          提交计划
        </el-button>
        <el-button v-if="canReject" type="danger" @click="rejectPlan">
          驳回
        </el-button>
      </div>
    </div>

    <!-- 计划事项详情对话框 -->
    <el-dialog v-model="itemDetailVisible" title="计划事项详情" width="520px">
      <el-form v-if="currentItem" :model="currentItem" label-position="top">
        <el-form-item label="标题">
          <el-input v-model="currentItem.title" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="currentItem.description" type="textarea" rows="4" />
        </el-form-item>
        <el-form-item label="交付物">
          <el-input v-model="currentItem.deliverable" placeholder="例如：报告、代码、文档等" />
        </el-form-item>
        <el-form-item label="截止日期">
          <el-date-picker v-model="currentItem.dueDate" type="date" format="YYYY-MM-DD" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="currentItem.priority">
            <el-option label="低" value="low" />
            <el-option label="中" value="medium" />
            <el-option label="高" value="high" />
            <el-option label="紧急" value="critical" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="itemDetailVisible = false">关闭</el-button>
        <el-button type="primary" @click="itemDetailVisible = false">确定</el-button>
      </template>
    </el-dialog>

    <!-- 验收标准对话框 -->
    <el-dialog v-model="showAcceptanceDialog" title="配置验收标准" width="520px">
      <div class="acceptance-editor">
        <div v-for="(criterion, idx) in form.acceptanceCriteria" :key="idx" class="criterion-edit">
          <el-input v-model="criterion.label" placeholder="标准名称" />
          <el-input v-model="criterion.description" placeholder="标准描述" />
          <el-button link type="danger" @click="removeCriterion(idx)">删除</el-button>
        </div>
        <el-button @click="addCriterion" type="primary" link>+ 添加标准</el-button>
      </div>
      <template #footer>
        <el-button @click="showAcceptanceDialog = false">取消</el-button>
        <el-button type="primary" @click="showAcceptanceDialog = false">完成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

interface PlanItem {
  id?: string
  title: string
  description?: string
  deliverable?: string
  dueDate?: string
  priority?: string
}

interface AcceptanceCriterion {
  label: string
  description: string
}

interface PlanForm {
  id?: string
  title: string
  type: 'MONTH' | 'WEEK' | 'DAY'
  status: string
  weight: number
  periodStart: string
  periodEnd: string
  items: PlanItem[]
  acceptanceCriteria: AcceptanceCriterion[]
}

const props = withDefaults(
  defineProps<{
    isNew?: boolean
    type?: 'MONTH' | 'WEEK' | 'DAY'
    initialData?: Partial<PlanForm>
    canEdit?: boolean
    canSubmit?: boolean
    canReject?: boolean
  }>(),
  {
    isNew: true,
    type: 'MONTH',
    canEdit: true,
    canSubmit: false,
    canReject: false,
  }
)

const emit = defineEmits<{
  save: [data: PlanForm]
  submit: [data: PlanForm]
  reject: [data: PlanForm]
  cancel: []
}>()

const isEditing = ref(false)
const saving = ref(false)
const itemDetailVisible = ref(false)
const showAcceptanceDialog = ref(false)
const currentItem = ref<PlanItem | null>(null)

const form = reactive<PlanForm>({
  title: '',
  type: props.type,
  status: 'draft',
  weight: 100,
  periodStart: '',
  periodEnd: '',
  items: [],
  acceptanceCriteria: [],
  ...props.initialData,
})

const typeLabel = computed(() => {
  const map = { MONTH: '月度计划', WEEK: '周计划', DAY: '日计划' }
  return map[form.type] || '计划'
})

const modeLabel = computed(() => (isEditing.value ? '编辑中' : '查看'))

const statusLabel = computed(() => {
  const map: Record<string, string> = {
    draft: '草稿',
    submitted: '已提交',
    approved: '已批准',
    rejected: '已驳回',
  }
  return map[form.status] || '未知'
})

const statusTag = computed(() => {
  const map: Record<string, string> = {
    draft: 'info',
    submitted: 'warning',
    approved: 'success',
    rejected: 'danger',
  }
  return map[form.status] || 'info'
})

function priorityLabel(priority: string): string {
  const map: Record<string, string> = {
    low: '低',
    medium: '中',
    high: '高',
    critical: '紧急',
  }
  return map[priority] || '中'
}

function toggleEdit() {
  isEditing.value = !isEditing.value
}

function onFormChange() {
  // 表单变化时的处理
}

function addItem() {
  form.items.push({
    id: `item_${Date.now()}`,
    title: '',
    description: '',
    deliverable: '',
    dueDate: '',
    priority: 'medium',
  })
}

function removeItem(index: number) {
  form.items.splice(index, 1)
}

function editItem(item: PlanItem) {
  currentItem.value = { ...item }
  itemDetailVisible.value = true
}

function addCriterion() {
  form.acceptanceCriteria.push({ label: '', description: '' })
}

function removeCriterion(index: number) {
  form.acceptanceCriteria.splice(index, 1)
}

async function saveDraft() {
  saving.value = true
  try {
    form.status = 'draft'
    emit('save', { ...form })
    ElMessage.success('草稿已保存')
  } finally {
    saving.value = false
  }
}

async function submitPlan() {
  if (!form.items.length) {
    ElMessage.warning('请至少添加一个计划事项')
    return
  }

  saving.value = true
  try {
    form.status = 'submitted'
    emit('submit', { ...form })
    ElMessage.success('计划已提交')
    isEditing.value = false
  } finally {
    saving.value = false
  }
}

async function rejectPlan() {
  ElMessageBox.confirm('确定要驳回这个计划吗?', '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(() => {
      emit('reject', { ...form })
      ElMessage.success('已驳回')
    })
    .catch(() => {})
}

function onCancel() {
  emit('cancel')
}
</script>

<style scoped lang="scss">
.plan-editor {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.plan-header-card,
.plan-content-card,
.acceptance-card {
  margin-bottom: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;

  .title {
    font-weight: 600;
    font-size: 1rem;
  }

  .actions {
    display: flex;
    gap: 0.75rem;
    align-items: center;
  }

  .count-badge {
    background: var(--el-fill-color);
    padding: 0.25rem 0.75rem;
    border-radius: 1rem;
    font-size: 0.875rem;
    color: var(--el-text-color-regular);
  }
}

.header-info {
  .info-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1.5rem;
  }

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

.plan-items {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.plan-item {
  padding: 1rem;
  border: 1px solid var(--el-border-color);
  border-radius: 0.5rem;
  background: var(--el-fill-color-light);

  .item-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.75rem;

    .item-number {
      min-width: 2rem;
      font-weight: 600;
      color: var(--el-color-primary);
    }

    .item-title-input {
      flex: 1;
    }

    .item-title {
      flex: 1;
      font-weight: 500;
      color: var(--el-text-color-primary);
    }

    .item-actions {
      display: flex;
      gap: 0.5rem;
    }
  }

  .item-description {
    margin-bottom: 0.75rem;
    color: var(--el-text-color-regular);
    font-size: 0.9375rem;
    line-height: 1.5;
  }

  .item-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;

    .meta-badge {
      display: inline-block;
      padding: 0.25rem 0.75rem;
      background: var(--el-fill-color);
      border-radius: 0.25rem;
      font-size: 0.875rem;
      color: var(--el-text-color-regular);

      &.priority-critical {
        background: rgba(245, 34, 45, 0.1);
        color: #f52a2d;
      }

      &.priority-high {
        background: rgba(230, 162, 60, 0.1);
        color: #e6a23c;
      }

      &.priority-medium {
        background: rgba(85, 139, 252, 0.1);
        color: #558bfc;
      }

      &.priority-low {
        background: rgba(103, 194, 58, 0.1);
        color: #67c23a;
      }
    }
  }
}

.empty-state {
  padding: 2rem 1rem;
  text-align: center;
  color: var(--el-text-color-placeholder);
}

.add-item-action {
  padding: 0.75rem 0;
  text-align: center;
}

.acceptance-editor {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;

  .criterion-edit {
    display: flex;
    gap: 0.75rem;
    align-items: center;

    :deep(.el-input) {
      flex: 1;
    }
  }
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
