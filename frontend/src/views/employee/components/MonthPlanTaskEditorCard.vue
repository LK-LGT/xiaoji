<template>
  <article class="task-editor-card" :class="{ 'has-errors': issues.length, 'is-expanded': expanded }" :data-task-key="taskKey">
    <span class="month-task-rail" aria-hidden="true"></span>
    <header class="editor-card-header">
      <el-button
        class="expand-toggle"
        text
        circle
        :icon="expanded ? ArrowDown : ArrowRight"
        :aria-label="expanded ? `收起任务 ${index + 1}` : `展开任务 ${index + 1}`"
        :aria-expanded="expanded"
        :aria-controls="bodyId"
        @click="$emit('toggle-expanded')"
      />
      <div class="task-number">任务 {{ String(index + 1).padStart(2, '0') }}</div>
      <button class="editor-title" type="button" :aria-expanded="expanded" :aria-controls="bodyId" @click="$emit('toggle-expanded')">
        <strong>{{ taskTitle }}</strong>
        <span>{{ completenessText }} · {{ weightText }} · {{ valueText }} · {{ periodText }}</span>
      </button>
      <div class="editor-actions">
        <el-button v-if="optimizing" link type="danger" @click="$emit('stop-optimize')">停止生成</el-button>
        <template v-else>
          <el-tooltip content="AI 优化" placement="top">
            <el-button class="task-tool" text circle type="primary" :icon="MagicStick" aria-label="AI 优化当前任务" :disabled="disabled" @click="$emit('optimize')" />
          </el-tooltip>
        </template>
        <el-tooltip content="复制任务" placement="top">
          <el-button class="task-tool" text circle :icon="CopyDocument" aria-label="复制当前任务" :disabled="disabled" @click="$emit('duplicate')" />
        </el-tooltip>
        <el-tooltip content="删除任务" placement="top">
          <el-button class="task-tool" text circle type="danger" :icon="Delete" aria-label="删除当前任务" :disabled="disabled" @click="$emit('remove')" />
        </el-tooltip>
      </div>
    </header>

    <div :id="bodyId" v-show="expanded" class="editor-card-body">
      <div class="editor-section">
        <div class="section-kicker">任务定义</div>
        <div class="field-grid">
          <el-form-item label="任务内容" class="span-2" :error="fieldError('taskContent')" :data-field-id="fieldId('taskContent')">
            <el-input v-model="item.taskContent" type="textarea" :rows="4" maxlength="5000" :disabled="disabled" placeholder="说明具体工作范围和关键动作" />
          </el-form-item>
        </div>
      </div>

      <div class="editor-section">
        <div class="section-kicker">交付验收</div>
        <div class="field-grid">
          <el-form-item label="交付物" class="span-2" :error="fieldError('deliverable')" :data-field-id="fieldId('deliverable')">
            <el-input v-model="item.deliverable" type="textarea" :rows="3" maxlength="500" :disabled="disabled" placeholder="例如：方案文档、上线功能、数据报表" />
          </el-form-item>
          <el-form-item label="质量" class="span-2" :error="fieldError('quality')" :data-field-id="fieldId('quality')">
            <el-input v-model="item.quality" type="textarea" :rows="3" maxlength="500" show-word-limit :disabled="disabled" placeholder="填写准确性、完整性、合规性或评审通过标准" />
          </el-form-item>
        </div>
      </div>

      <div class="editor-section compact-section">
        <div class="section-kicker">排期与考核</div>
        <div class="schedule-grid">
          <el-form-item label="预估开始日期" :error="fieldError('estimatedStartDate')" :data-field-id="fieldId('estimatedStartDate')">
            <el-date-picker v-model="item.estimatedStartDate" type="date" format="M.D" value-format="YYYY-MM-DD" :disabled="disabled" :disabled-date="disabledStartDate" placeholder="选择开始日期" />
          </el-form-item>
          <el-form-item label="预估结束日期" :error="fieldError('estimatedEndDate')" :data-field-id="fieldId('estimatedEndDate')">
            <el-date-picker v-model="item.estimatedEndDate" type="date" format="M.D" value-format="YYYY-MM-DD" :disabled="disabled" :disabled-date="disabledEndDate" placeholder="选择结束日期" />
          </el-form-item>
          <el-form-item label="绩效权重" :error="fieldError('performanceWeight')" :data-field-id="fieldId('performanceWeight')">
            <el-input-number v-model="item.performanceWeight" placeholder="0.00" :disabled="disabled" :min="0" :max="100" :step="1" :precision="2" controls-position="right" />
          </el-form-item>
          <el-form-item label="预估价值（元）" :error="fieldError('estimatedValue')" :data-field-id="fieldId('estimatedValue')">
            <el-input-number v-model="item.estimatedValue" placeholder="0.00" :disabled="disabled" :min="0" :max="9999999999.99" :step="100" :precision="2" controls-position="right" />
          </el-form-item>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts" generic="T extends { taskName: string; taskContent: string; deliverable: string; quality: string; estimatedStartDate: string; estimatedEndDate: string; performanceWeight: number | null; estimatedValue: number | null }">
import { ArrowDown, ArrowRight, CopyDocument, Delete, MagicStick } from '@element-plus/icons-vue'
import { computed } from 'vue'
import { formatMonthPlanPeriod } from '@/utils/monthPlanPeriod'

interface TaskValidationIssue { field?: string; message: string }

const props = defineProps<{ item: T; index: number; taskKey: string; expanded: boolean; disabled: boolean; optimizing: boolean; issues: TaskValidationIssue[]; disabledStartDate: (date: Date) => boolean; disabledEndDate: (date: Date) => boolean }>()
defineEmits<{ optimize: []; 'stop-optimize': []; duplicate: []; remove: []; 'toggle-expanded': [] }>()

const required = ['taskContent', 'deliverable', 'quality', 'estimatedStartDate', 'estimatedEndDate'] as const
const completeCount = computed(() => required.filter((key) => String(props.item[key] || '').trim()).length
  + (Number(props.item.performanceWeight) > 0 ? 1 : 0)
  + (Number(props.item.estimatedValue) >= 0 ? 1 : 0))
const completenessText = computed(() => completeCount.value === 7 ? '信息完整' : `还差 ${7 - completeCount.value} 项`)
const weightText = computed(() => props.item.performanceWeight == null ? '权重未填' : `${props.item.performanceWeight}%`)
const valueText = computed(() => props.item.estimatedValue == null ? '价值未填' : `¥${Number(props.item.estimatedValue).toLocaleString('zh-CN')}`)
const taskTitle = computed(() => String(props.item.taskContent || '').replace(/\s+/g, ' ').trim().slice(0, 48) || '未填写任务内容')
const periodText = computed(() => formatMonthPlanPeriod(
  props.item.estimatedStartDate,
  props.item.estimatedEndDate,
  '未定时间段',
))
const bodyId = computed(() => `task-card-body-${props.taskKey}`)
const fieldId = (field: string) => `task-${props.index}-${field}`
const fieldError = (field: string) => props.issues.find((issue) => issue.field === field)?.message || ''
</script>

<style scoped>
.task-editor-card { position:relative; overflow:hidden; border:1px solid var(--line); border-radius:8px; background:#fff; }
.month-task-rail { position:absolute; inset:0 auto 0 0; z-index:1; width:4px; background:#2d776c; }
.task-editor-card.has-errors .month-task-rail { background:#c65346; }
.editor-card-header { display:grid; grid-template-columns:32px auto minmax(0,1fr) auto; gap:12px; align-items:center; padding:13px 16px 13px 18px; border-bottom:1px solid #e9ede8; background:#f8faf8; }
.task-editor-card:not(.is-expanded) .editor-card-header { border-bottom-color:transparent; }
.expand-toggle { color:var(--blue); }
.task-number { color:#2d776c; font:800 14px/1 "IBM Plex Mono",monospace; letter-spacing:0; }
.editor-title { display:grid; min-width:0; gap:4px; padding:0; border:0; color:inherit; background:transparent; text-align:left; cursor:pointer; }
.editor-title:focus-visible { border-radius:4px; outline:2px solid var(--blue); outline-offset:3px; }
.editor-title strong { overflow:hidden; color:var(--ink); font-size:16px; text-overflow:ellipsis; white-space:nowrap; }
.editor-title span { overflow:hidden; color:var(--muted); font-size:14px; text-overflow:ellipsis; white-space:nowrap; }
.editor-actions { display:flex; align-items:center; gap:2px; white-space:nowrap; }
.editor-actions :deep(.el-button + .el-button) { margin-left:0; }
.task-tool { width:32px; height:32px; }
.editor-section { padding:18px; border-bottom:1px solid #edf0eb; }
.editor-section:last-child { border-bottom:0; }
.section-kicker { margin-bottom:14px; color:#587068; font-size:16px; font-weight:800; letter-spacing:0; }
.field-grid { display:grid; grid-template-columns:1fr 1fr; gap:0 16px; }
.field-grid :deep(.el-form-item__label) { flex:0 0 76px; width:76px; }
.span-2 { grid-column:1/-1; }
.schedule-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
.schedule-grid :deep(.el-input-number),.schedule-grid :deep(.el-date-editor) { width:100%; }
.schedule-grid :deep(.el-input-number .el-input__inner::placeholder) { color:#9aaba4; opacity:1; }
.more-settings { margin-top:2px; border:0; }
.more-settings :deep(.el-collapse-item__header) { height:36px; color:var(--muted); font-size:12px; border:0; }
.more-settings :deep(.el-collapse-item__wrap) { border:0; }
.more-settings :deep(.el-collapse-item__content) { padding:8px 0 0; }
:deep(.el-form-item) { margin-bottom:16px; }
:deep(.el-form-item__label) { color:#52645f; font-size:16px; font-weight:700; }
@media (max-width:900px) {
  .editor-card-header { grid-template-columns:32px minmax(0,1fr); }
  .task-number { align-self:center; }
  .editor-title { grid-column:2; }
  .editor-actions { grid-column:1/-1; justify-content:flex-end; }
  .schedule-grid { grid-template-columns:1fr; }
}
@media (prefers-reduced-motion:reduce) {
  .task-editor-card,.editor-card-header { transition:none; }
}
</style>
