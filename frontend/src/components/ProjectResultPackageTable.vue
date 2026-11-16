<template>
  <div class="project-result-package-table">
    <el-table
      v-loading="loading"
      :data="normalizedPackages"
      row-key="groupId"
      :empty-text="emptyText"
    >
      <el-table-column label="项目组" min-width="160">
        <template #default="{ row }">
          <div class="project-group-name">
            <span class="project-group-mark">{{ groupInitial(row.groupName) }}</span>
            <span><strong>{{ row.groupName }}</strong><small>{{ row.departmentName || '未归属部门' }}</small></span>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="planMonth" label="成果月份" width="110" />
      <el-table-column label="已完成/总人数" min-width="205">
        <template #default="{ row }">
          <div class="package-progress">
            <strong>{{ row.completedMemberCount }}/{{ row.totalMemberCount }}</strong>
            <el-progress
              :percentage="packagePercentage(row)"
              :stroke-width="6"
              :show-text="false"
              :status="row.contentAvailable ? 'success' : undefined"
            />
          </div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="packageStatusType(row)" effect="plain">{{ packageStatusLabel(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="待完成人员" min-width="245">
        <template #default="{ row }">
          <span v-if="row.contentAvailable" class="package-complete-text">全员成果已通过</span>
          <span v-else-if="!row.pendingMembers.length" class="package-pending-empty">暂无成员</span>          <el-tooltip v-else :content="pendingMemberText(row)" placement="top" :show-after="250">
            <span class="package-pending-text">{{ pendingMemberPreview(row) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right" align="center">
        <template #default="{ row }">
          <el-tooltip
            :disabled="row.contentAvailable"
            content="需项目组所有员工和领导的成果全部审批通过后开放"
            placement="top"
          >
            <span>
              <el-button
                link
                type="primary"
                :icon="row.contentAvailable ? View : Lock"
                :disabled="!row.contentAvailable"
                @click="openPackage(row)"
              >
                查看整组成果
              </el-button>
            </span>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer
      v-model="detailVisible"
      title="项目组完整成果"
      size="min(1080px, calc(100vw - 24px))"
      @closed="closeDetail"
    >
      <el-skeleton v-if="detailLoading" :rows="8" animated />
      <div v-else-if="detail" class="package-result-detail">
        <header class="package-detail-summary">
          <div>
            <span>{{ detailInfo.departmentName || '所属部门' }}</span>
            <h2>{{ detailInfo.groupName }}</h2>
            <p>{{ detailInfo.planMonth }} · 全组 {{ detailInfo.totalMemberCount }} 人成果均已审批通过</p>
          </div>
          <el-tag type="success" effect="plain">完整开放</el-tag>
        </header>

        <el-table :data="detailRows" border empty-text="暂无成果记录">
          <el-table-column type="index" label="#" width="55" />
          <el-table-column label="成员" width="130">
            <template #default="{ row }">
              <div class="result-owner"><strong>{{ row.employeeName }}</strong><small>{{ row.employeeNo || '未填写工号' }}</small></div>
            </template>
          </el-table-column>
          <el-table-column label="计划与成果" min-width="240">
            <template #default="{ row }">
              <div class="result-title"><strong>{{ row.resultTitle }}</strong><small>{{ row.planNo }}</small></div>
            </template>
          </el-table-column>
          <el-table-column prop="resultDate" label="成果日期" width="115" />
          <el-table-column label="成果证据" min-width="250">
            <template #default="{ row }">
              <div v-if="row.evidences.length" class="result-evidence-list">
                <el-button
                  v-for="evidence in row.evidences"
                  :key="evidence.fileId"
                  link
                  type="primary"
                  :icon="Download"
                  :loading="downloadingEvidenceId === evidence.fileId"
                  :disabled="downloadingEvidenceId !== null"
                  @click="downloadEvidence(row.id, evidence)"
                >{{ evidence.fileName }}</el-button>
              </div>
              <span v-else class="package-pending-empty">暂无证据</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="95">
            <template #default><el-tag type="success" effect="plain">已通过</el-tag></template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Download, Lock, View } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  downloadDepartmentResultEvidenceApi,
  getProjectResultPackageApi,
  type ProjectResultPackage,
  type ProjectResultPackageDetail,
} from '@/api/department'
import { errorMessage, type EvidenceFile } from '@/api/performance'
import { saveBlob } from '@/utils/download'

const props = withDefaults(defineProps<{
  packages: ProjectResultPackage[]
  loading?: boolean
  emptyText?: string
}>(), {
  loading: false,
  emptyText: '当前范围暂无项目组',
})

/**
 * 把后端返回的包**归一化**成模板可安全使用的结构。
 *
 * ⚠️ BUG-R6-25 防御层：后端 `/department/project-result-packages` 此前返回的
 * 字段与前端契约完全对不上（`{totalResultCount, approvedCount}` vs
 * `{totalMemberCount, completedMemberCount, status, contentAvailable,
 * pendingMembers}`），导致模板里的 `row.pendingMembers.length` 抛 TypeError，
 * **把整个 ResultConfirm 页面渲染打断**（表现为"查不到任何成果"）。
 *
 * 契约失配不应该让整个页面挂掉 —— 这里统一兜底：缺的字段补默认值，
 * 数组字段强制为数组。
 */
function normalizePackage(raw: Partial<ProjectResultPackage> & { groupId: string }): ProjectResultPackage {
  const pendingMembers = Array.isArray(raw.pendingMembers) ? raw.pendingMembers : []
  const totalMemberCount = Number(raw.totalMemberCount ?? 0) || 0
  const completedMemberCount = Number(raw.completedMemberCount ?? 0) || 0
  const status = raw.status ?? (totalMemberCount === 0 ? 'EMPTY' : 'COLLECTING')
  const contentAvailable = raw.contentAvailable ?? status === 'OPEN'
  return {
    groupId: String(raw.groupId ?? ''),
    groupName: raw.groupName ?? '未命名项目组',
    departmentName: raw.departmentName ?? '',
    planMonth: raw.planMonth ?? '',
    totalMemberCount,
    completedMemberCount,
    status,
    contentAvailable,
    pendingMembers,
    completedAt: raw.completedAt,
  }
}

/** 归一化后的包列表（模板一律用这个，不要直接用 props.packages）。 */
const normalizedPackages = computed(() => (props.packages ?? []).map(normalizePackage))

/**
 * 详情抽屉里的包信息（归一化）。
 *
 * ⚠️ BUG-R6-25 防御层：后端此前返回的是**裸包**而不是 `{packageInfo, results}`
 * 包裹结构，`detail.packageInfo.departmentName` 会直接抛
 * `Cannot read properties of undefined`。
 */
const detailInfo = computed(() => normalizePackage(detail.value?.packageInfo ?? ({ groupId: '' } as never)))

/** 详情抽屉里的成果行（`evidences` 强制为数组）。 */
const detailRows = computed(() =>
  (detail.value?.results ?? []).map((row) => ({
    ...row,
    evidences: Array.isArray(row.evidences) ? row.evidences : [],
  })),
)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<ProjectResultPackageDetail>()
const downloadingEvidenceId = ref<number | null>(null)

async function openPackage(row: ProjectResultPackage) {
  if (!row.contentAvailable) return
  detailVisible.value = true
  detailLoading.value = true
  detail.value = undefined
  try {
    detail.value = await getProjectResultPackageApi(row.groupId, { planMonth: row.planMonth })
  } catch (error) {
    detailVisible.value = false
    ElMessage.error(errorMessage(error))
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detail.value = undefined
  downloadingEvidenceId.value = null
}

async function downloadEvidence(resultId: string, evidence: EvidenceFile) {
  if (downloadingEvidenceId.value !== null) return
  downloadingEvidenceId.value = evidence.fileId
  try {
    saveBlob(await downloadDepartmentResultEvidenceApi(resultId, evidence.fileId), evidence.fileName)
    ElMessage.success('成果证据已下载')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    downloadingEvidenceId.value = null
  }
}

function packagePercentage(row: ProjectResultPackage) {
  if (!row.totalMemberCount) return 0
  return Math.round((row.completedMemberCount / row.totalMemberCount) * 100)
}

function packageStatusLabel(row: ProjectResultPackage) {
  if (row.status === 'OPEN') return '完整开放'
  if (row.status === 'EMPTY') return '暂无成员'
  return '等待集齐'
}

function packageStatusType(row: ProjectResultPackage): 'success' | 'warning' | 'info' {
  if (row.status === 'OPEN') return 'success'
  if (row.status === 'EMPTY') return 'info'
  return 'warning'
}

function pendingMemberText(row: ProjectResultPackage) {
  // ⚠️ BUG-R6-25 防御：pendingMembers 可能缺失（契约失配），不能直接 .map。
  const members = Array.isArray(row.pendingMembers) ? row.pendingMembers : []
  return members.map((member) => (
    `${member.employeeName}（${member.positionName || '项目组成员'}，${member.statusLabel}，${member.confirmedResultCount}/${member.expectedResultCount}项）`
  )).join('、')
}

function pendingMemberPreview(row: ProjectResultPackage) {
  const members = Array.isArray(row.pendingMembers) ? row.pendingMembers : []
  const names = members.slice(0, 3)
    .map((member) => `${member.employeeName}（${member.positionName || '成员'} · ${member.statusLabel}）`).join('、')
  return members.length > 3 ? `${names} 等 ${members.length} 人` : names
}

function groupInitial(name: string) {
  return name?.trim().slice(0, 1) || '组'
}
</script>

<style scoped>
.project-result-package-table { overflow: hidden; border: 1px solid var(--line); border-radius: 7px; }
.project-group-name { display: flex; min-width: 0; align-items: center; gap: 10px; }
.project-group-name > span:last-child,
.project-group-name strong,
.project-group-name small,
.result-owner,
.result-title { display: block; min-width: 0; }
.project-group-name strong,
.result-owner strong,
.result-title strong { display: block; overflow: hidden; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.project-group-name small,
.result-owner small,
.result-title small { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; }
.project-group-mark { display: grid; width: 30px; height: 30px; flex: 0 0 auto; place-items: center; border-radius: 5px; color: #fff; background: #315e59; font-size: 12px; font-weight: 700; }
.package-progress { display: grid; grid-template-columns: 42px minmax(80px, 1fr); align-items: center; gap: 10px; }
.package-progress strong { color: var(--ink); font: 700 12px/1 "IBM Plex Mono", "Cascadia Mono", monospace; }
.package-complete-text { color: var(--green); font-weight: 650; }
.package-pending-text { display: block; overflow: hidden; color: #8b5b32; text-overflow: ellipsis; white-space: nowrap; }
.package-pending-empty { color: var(--muted); }
.package-result-detail { display: grid; gap: 18px; }
.package-detail-summary { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--line); }
.package-detail-summary span { color: var(--muted); font-size: 12px; }
.package-detail-summary h2 { margin: 4px 0 0; color: var(--ink); font-size: 18px; }
.package-detail-summary p { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
.result-evidence-list { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.result-evidence-list .el-button { max-width: 100%; margin: 0; }

@media (max-width: 760px) {
  .package-detail-summary { flex-direction: column; }
}
</style>
