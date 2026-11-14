<template>
  <div class="classification-form">
    <div class="classification-heading">
      <div><strong>入库分类</strong><span>分类会用于 AI 检索范围和匹配优先级</span></div>
      <small>均需填写</small>
    </div>

    <el-form label-position="top" :disabled="disabled">
      <div class="classification-grid">
        <el-form-item label="适用角色" required>
          <el-checkbox-group :model-value="modelValue.audienceCodes" @change="update('audienceCodes', $event)">
            <el-checkbox value="MANAGEMENT">管理端</el-checkbox>
            <el-checkbox value="EMPLOYEE">员工端</el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <el-form-item label="适用范围" required>
          <el-segmented
            :model-value="modelValue.scopeType"
            :options="scopeOptions"
            block
            @change="changeScope"
          />
        </el-form-item>

        <el-form-item v-if="modelValue.scopeType !== 'GLOBAL'" :label="modelValue.scopeType === 'PROJECT' ? '指定项目' : '指定部门'" required>
          <el-select
            :model-value="modelValue.scopeOrgId"
            filterable
            clearable
            :placeholder="modelValue.scopeType === 'PROJECT' ? '选择项目' : '选择部门或小组'"
            @change="update('scopeOrgId', $event)"
          >
            <el-option v-for="item in scopeOrgOptions" :key="item.id" :label="item.pathLabel" :value="item.id" />
          </el-select>
        </el-form-item>

        <el-form-item label="文档类型" required>
          <el-select :model-value="modelValue.knowledgeType" placeholder="选择文档类型" @change="update('knowledgeType', $event)">
            <el-option v-for="item in knowledgeTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>

        <el-form-item label="约束等级" required>
          <el-select :model-value="modelValue.bindingLevel" placeholder="选择约束等级" @change="update('bindingLevel', $event)">
            <el-option label="正式规范" value="NORMATIVE" />
            <el-option label="参考资料" value="REFERENCE" />
            <el-option label="示例案例" value="EXAMPLE" />
          </el-select>
        </el-form-item>
      </div>

      <el-form-item class="business-scenes" label="适用业务" required>
        <el-checkbox-group :model-value="modelValue.appliesTo" @change="update('appliesTo', $event)">
          <el-checkbox v-for="item in businessOptions" :key="item.value" :value="item.value">{{ item.label }}</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeScopeType, KnowledgeUploadMetadata } from '@/api/knowledge'

interface OrgOption {
  id: number
  name?: string
  label?: string
  orgType: string
  children?: OrgOption[]
}

const props = defineProps<{
  modelValue: KnowledgeUploadMetadata
  orgs: OrgOption[]
  disabled?: boolean
}>()

const emit = defineEmits<{ (event: 'update:modelValue', value: KnowledgeUploadMetadata): void }>()

const scopeOptions = [
  { label: '全公司', value: 'GLOBAL' },
  { label: '指定部门', value: 'DEPARTMENT' },
  { label: '指定项目', value: 'PROJECT' },
]

const knowledgeTypeOptions = [
  { label: '历史计划', value: 'HISTORICAL_PLAN' },
  { label: '岗位说明', value: 'JOB_DESCRIPTION' },
  { label: '部门目标', value: 'DEPARTMENT_GOAL' },
  { label: '领导要求', value: 'LEADER_REQUIREMENT' },
  { label: '项目资料', value: 'PROJECT_MATERIAL' },
  { label: '制度规范', value: 'POLICY' },
  { label: '验收标准', value: 'ACCEPTANCE_STANDARD' },
  { label: '参考案例', value: 'REFERENCE_CASE' },
]

const businessOptions = [
  { label: '月计划生成', value: 'MONTH_PLAN' },
  { label: '周计划生成', value: 'WEEK_PLAN' },
  { label: '日计划生成', value: 'DAY_PLAN' },
  { label: '计划检查', value: 'PLAN_CHECK' },
  { label: '计划审批', value: 'APPROVAL' },
  { label: '成果提交', value: 'RESULT' },
  { label: '成果确认', value: 'RESULT_CONFIRM' },
]

const scopeOrgOptions = computed(() => flattenOrgs(props.orgs).filter((item) => props.modelValue.scopeType === 'PROJECT'
  ? item.orgType === 'PROJECT_GROUP'
  : item.orgType === 'DEPARTMENT' || item.orgType === 'GROUP'))

function flattenOrgs(nodes: OrgOption[], parents: string[] = []): Array<OrgOption & { pathLabel: string }> {
  return nodes.flatMap((node) => {
    const name = node.name || node.label || `组织 ${node.id}`
    const path = [...parents, name]
    return [{ ...node, pathLabel: path.join(' / ') }, ...flattenOrgs(node.children || [], path)]
  })
}

function update<K extends keyof KnowledgeUploadMetadata>(key: K, value: KnowledgeUploadMetadata[K]) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function changeScope(value: string | number | boolean) {
  emit('update:modelValue', {
    ...props.modelValue,
    scopeType: String(value) as KnowledgeScopeType,
    scopeOrgId: undefined,
  })
}
</script>

<style scoped>
.classification-form { margin-bottom: 18px; padding-block: 14px 4px; border-block: 1px solid var(--line); }
.classification-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.classification-heading > div { display: grid; gap: 4px; }
.classification-heading strong { color: var(--ink); font-size: 14px; }
.classification-heading span, .classification-heading small { color: var(--muted); font-size: 11px; }
.classification-heading small { white-space: nowrap; }
.classification-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 14px; }
.classification-grid :deep(.el-select), .classification-grid :deep(.el-segmented) { width: 100%; }
.classification-form :deep(.el-form-item) { margin-bottom: 14px; }
.classification-form :deep(.el-form-item__label) { color: #435851; font-size: 12px; }
.classification-form :deep(.el-checkbox-group) { display: flex; flex-wrap: wrap; gap: 4px 15px; }
.classification-form :deep(.el-checkbox) { margin-right: 0; }
.business-scenes :deep(.el-form-item__content) { min-height: 32px; }
@media (max-width: 760px) { .classification-grid { grid-template-columns: 1fr; } }
</style>
