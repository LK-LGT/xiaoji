<template>
  <el-dialog
    :model-value="modelValue"
    class="version-history-dialog"
    width="min(1200px, calc(100vw - 40px))"
    top="6vh"
    append-to-body
    :close-on-click-modal="!restoring"
    :close-on-press-escape="!restoring"
    @update:model-value="emit('update:modelValue', $event)"
    @open="loadHistory"
  >
    <template #header>
      <div class="dialog-heading">
        <div>
          <h2>历史版本</h2>
          <span>{{ periodKey }}</span>
        </div>
        <el-tooltip content="刷新版本" placement="top">
          <el-button :icon="Refresh" circle aria-label="刷新版本" :loading="loading" @click="loadHistory" />
        </el-tooltip>
      </div>
    </template>

    <div class="history-review-workbench">
      <PlanDraftVersionRail
        :active-group="activeGroup"
        :group-options="versionGroups"
        :versions="visibleVersions"
        :selected-version-id="selectedVersionId"
        :loading="loading"
        :error="historyError"
        :empty-text="activeGroup === 'saved' ? '暂无主动保存的历史版本' : '暂无 AI 生成记录'"
        :source-labels="sourceLabels"
        @update:active-group="activeGroup = $event"
        @select="selectVersion"
        @retry="loadHistory"
      />

      <section class="review-pane">
        <header class="review-heading">
          <template v-if="selected">
            <div class="review-heading__identity">
              <strong>V{{ selected.versionNo }}</strong>
              <div>
                <h3>{{ sourceLabels[selected.sourceType] }}</h3>
                <time>{{ formatFullTime(selected.createdAt) }}</time>
              </div>
            </div>
            <el-tag :type="changed ? 'warning' : 'success'" effect="plain">
              {{ changed ? '与当前计划不同' : '与当前计划一致' }}
            </el-tag>
          </template>
          <div v-else class="review-heading__placeholder">
            <h3>版本内容</h3>
            <span>从版本列表中选择一条记录</span>
          </div>
        </header>

        <main class="review-content" v-loading="detailLoading">
          <el-alert v-if="detailError" class="detail-error" type="error" :closable="false" show-icon :title="detailError">
            <template #default>
              <el-button v-if="selectedVersionSummary" link type="primary" @click="selectVersion(selectedVersionSummary)">
                重新加载
              </el-button>
            </template>
          </el-alert>

          <template v-if="selected">
            <template v-if="planType === 'MONTH'">
              <MonthPlanVersionDiff :diff="monthDiff" />
            </template>

            <section v-else class="plan-snapshot-overview" aria-label="版本概览">
              <div v-for="entry in previewEntries" :key="entry.label">
                <span>{{ entry.label }}</span>
                <strong>{{ entry.value }}</strong>
              </div>
            </section>

            <el-alert
              v-if="selected.baseVersionId"
              class="restore-source-note"
              type="info"
              :closable="false"
              :title="`此版本由旧版本 #${selected.baseVersionId} 恢复后保存`"
            />

            <details class="technical-details">
              <summary>技术信息</summary>
              <pre class="snapshot-json">{{ formattedSnapshot }}</pre>
            </details>
          </template>

          <el-empty
            v-else-if="!detailLoading && !detailError"
            :description="visibleVersions.length ? '选择一个版本查看内容' : '当前分组暂无可查看的版本'"
            :image-size="64"
          />
        </main>

        <footer v-if="selected" class="restore-bar">
          <p><strong>恢复不会立即保存</strong><span>仅替换当前编辑内容，确认无误后仍需手动保存。</span></p>
          <el-tooltip :disabled="editable" content="当前计划不可编辑" placement="top">
            <span>
              <el-button type="primary" :loading="restoring" :disabled="!editable" @click="restoreSelected">
                <el-icon><RefreshLeft /></el-icon>
                恢复到当前计划
              </el-button>
            </span>
          </el-tooltip>
        </footer>
      </section>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Refresh, RefreshLeft } from '@element-plus/icons-vue'
import { getPlanDraftVersionApi, listPlanDraftVersionsApi, restorePlanDraftVersionApi, type PlanDraftVersionDetail, type PlanDraftVersionPlanType, type PlanDraftVersionSource, type PlanDraftVersionSummary } from '@/api/planDraftVersions'
import MonthPlanVersionDiff from './MonthPlanVersionDiff.vue'
import PlanDraftVersionRail from './PlanDraftVersionRail.vue'
import { arePlanSnapshotsEqual, compareMonthPlanSnapshots, type MonthPlanDiff } from './planDraftVersionComparison'
// ⚠️ **纯逻辑已抽到 `@/utils/planDraftVersionDrawer`**（CI-5 阶段 2 第五轮）⇒ 这里只做转发。
import {
  buildPlanDraftPreviewEntries,
  buildPlanDraftVersionGroupOptions,
  findPlanDraftVersionById,
  formatPlanDraftFullTime,
  formatPlanDraftSnapshot,
  isPlanDraftChanged,
  selectVisiblePlanDraftVersions,
} from '@/utils/planDraftVersionDrawer'

const props = defineProps<{
  modelValue: boolean
  planType: PlanDraftVersionPlanType
  periodKey: string
  currentSnapshot: Record<string, unknown>
  editable: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  restore: [snapshot: Record<string, unknown>, version: PlanDraftVersionSummary]
  loaded: [latestVersionNo: number | undefined]
}>()

const loading = ref(false)
const detailLoading = ref(false)
const restoring = ref(false)
const historyError = ref('')
const detailError = ref('')
const versions = ref<PlanDraftVersionSummary[]>([])
const selected = ref<PlanDraftVersionDetail>()
const selectedVersionId = ref<number>()
const activeGroup = ref<'saved' | 'ai'>('saved')
let detailRequest = 0

const sourceLabels: Record<PlanDraftVersionSource, string> = {
  AI_GENERATE: 'AI 生成',
  AI_OPTIMIZE: 'AI 优化',
  MANUAL_SAVE: '手动保存',
  RESTORED_SAVE: '恢复后保存',
}
// ⚠️ **纯逻辑已抽到 `@/utils/planDraftVersionDrawer`**（CI-5 阶段 2 第五轮）⇒ 这里只做转发。
const savedVersions = computed(() => selectVisiblePlanDraftVersions(versions.value, 'saved'))
const aiVersions = computed(() => selectVisiblePlanDraftVersions(versions.value, 'ai'))
const visibleVersions = computed(() => activeGroup.value === 'saved' ? savedVersions.value : aiVersions.value)
const selectedVersionSummary = computed(() => findPlanDraftVersionById(visibleVersions.value, selectedVersionId.value))
const versionGroups = computed<Array<{ label: string; value: 'saved' | 'ai' }>>(() => buildPlanDraftVersionGroupOptions(versions.value))
const emptyMonthDiff: MonthPlanDiff = {
  items: [],
  counts: { added: 0, modified: 0, removed: 0 },
  unchanged: true,
}
const formattedSnapshot = computed(() => formatPlanDraftSnapshot(selected.value?.snapshot))
const monthDiff = computed(() => selected.value
  ? compareMonthPlanSnapshots(selected.value.snapshot, props.currentSnapshot)
  : emptyMonthDiff)
const changed = computed(() => isPlanDraftChanged({
  planType: props.planType,
  hasSnapshot: Boolean(selected.value),
  monthDiffUnchanged: monthDiff.value.unchanged,
  snapshotsEqual: arePlanSnapshotsEqual(props.planType, selected.value?.snapshot ?? {}, props.currentSnapshot),
}))
const previewEntries = computed(() => buildPlanDraftPreviewEntries(props.planType, selected.value?.snapshot))

async function loadHistory() {
  if (!props.periodKey || loading.value) return
  const previousId = selectedVersionId.value
  historyError.value = ''
  loading.value = true
  try {
    versions.value = await listPlanDraftVersionsApi(props.planType, props.periodKey)
    emit('loaded', versions.value[0]?.versionNo)
    const previous = visibleVersions.value.find((version) => version.id === previousId)
    if (previous) await selectVersion(previous, true)
    else await selectLatestVisibleVersion()
  } catch (error) {
    versions.value = []
    selected.value = undefined
    selectedVersionId.value = undefined
    historyError.value = error instanceof Error ? error.message : '历史版本加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function selectLatestVisibleVersion() {
  const latest = visibleVersions.value[0]
  if (!latest) {
    selected.value = undefined
    selectedVersionId.value = undefined
    return
  }
  await selectVersion(latest)
}

async function selectVersion(version: PlanDraftVersionSummary, force = false) {
  if (!force && selectedVersionId.value === version.id && selected.value?.id === version.id) return
  const request = ++detailRequest
  selectedVersionId.value = version.id
  selected.value = undefined
  detailError.value = ''
  detailLoading.value = true
  try {
    const detail = await getPlanDraftVersionApi(version.id)
    if (request === detailRequest) selected.value = detail
  } catch (error) {
    if (request !== detailRequest) return
    detailError.value = error instanceof Error ? error.message : '版本内容加载失败，请重新选择'
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}

async function restoreSelected() {
  if (!selected.value || !props.editable || restoring.value) return
  restoring.value = true
  detailError.value = ''
  try {
    const version = await restorePlanDraftVersionApi(selected.value.id, props.planType, props.periodKey)
    emit('restore', version.snapshot, version)
    emit('update:modelValue', false)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '版本恢复失败，请稍后重试'
  } finally {
    restoring.value = false
  }
}

function formatFullTime(value: string) {
  return formatPlanDraftFullTime(value)
}

watch(activeGroup, () => {
  detailRequest += 1
  selected.value = undefined
  selectedVersionId.value = undefined
  detailError.value = ''
  detailLoading.value = false
  void selectLatestVisibleVersion()
})
watch(() => [props.planType, props.periodKey], () => {
  versions.value = []
  selected.value = undefined
  selectedVersionId.value = undefined
  activeGroup.value = 'saved'
  if (props.modelValue) void loadHistory()
})
</script>

<style scoped>
:global(.version-history-dialog) { max-width:1200px; overflow:hidden; border-radius:8px; }
:global(.version-history-dialog .el-dialog__header) { margin:0; padding:14px 20px; border-bottom:1px solid #dde5e2; }
:global(.version-history-dialog .el-dialog__headerbtn) { top:12px; right:14px; width:36px; height:36px; }
:global(.version-history-dialog .el-dialog__body) { padding:0; overflow:hidden; }
.dialog-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; padding-right:42px; }
.dialog-heading > div { display:flex; align-items:baseline; gap:10px; min-width:0; }
.dialog-heading h2 { margin:0; color:#203832; font-size:18px; line-height:1.4; }
.dialog-heading span { overflow:hidden; color:#72817d; font:12px "Cascadia Mono",monospace; text-overflow:ellipsis; white-space:nowrap; }
.history-review-workbench { display:grid; grid-template-columns:264px minmax(0,1fr); height:78vh; min-height:min(520px, calc(100dvh - 110px)); max-height:760px; background:#fff; }
.review-pane { display:grid; grid-template-rows:auto minmax(0,1fr) auto; min-width:0; min-height:0; }
.review-heading { display:flex; align-items:center; justify-content:space-between; gap:18px; min-height:68px; padding:12px 22px; border-bottom:1px solid #e1e7e4; background:#fff; }
.review-heading__identity { display:flex; align-items:center; gap:13px; min-width:0; }
.review-heading__identity > strong { flex:0 0 auto; color:#27685e; font:700 22px "Cascadia Mono",monospace; }
.review-heading__identity > div { display:grid; gap:2px; min-width:0; }
.review-heading h3 { margin:0; color:#203832; font-size:15px; line-height:1.4; }
.review-heading time,.review-heading__placeholder span { color:#72817d; font:12px "Cascadia Mono",monospace; }
.review-heading__placeholder { display:grid; gap:2px; }
.review-content { min-height:0; padding:18px 22px 24px; overflow-y:auto; scrollbar-gutter:stable; }
.detail-error { margin-bottom:14px; }
.plan-snapshot-overview { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); border:1px solid #dfe6e3; border-radius:6px; }
.plan-snapshot-overview div { display:grid; gap:6px; min-width:0; padding:13px 15px; border-left:1px solid #e3e9e6; }
.plan-snapshot-overview div:first-child { border-left:0; }
.plan-snapshot-overview span { color:#71817c; font-size:12px; }
.plan-snapshot-overview strong { overflow-wrap:anywhere; color:#293e38; font-size:14px; line-height:1.55; }
.restore-source-note { margin-top:14px; }
.technical-details { margin-top:16px; color:#64748b; font-size:12px; }
.technical-details summary { width:max-content; cursor:pointer; }
.technical-details summary:focus-visible { outline:2px solid #2d776c; outline-offset:3px; }
.snapshot-json { max-height:220px; margin:8px 0 0; padding:12px; overflow:auto; border:1px solid #e0e6e3; background:#f7f8f6; color:#33413d; font:12px/1.6 "Cascadia Mono",monospace; white-space:pre-wrap; word-break:break-word; }
.restore-bar { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:12px 22px; border-top:1px solid #dfe6e3; background:#fbfcfc; box-shadow:0 -8px 18px rgb(30 58 50 / 4%); }
.restore-bar p { display:grid; gap:2px; margin:0; }
.restore-bar p strong { color:#334943; font-size:13px; }
.restore-bar p span { color:#71817c; font-size:12px; }
.restore-bar .el-button { min-width:156px; }
@media (max-width:760px) {
  :global(.version-history-dialog) { width:calc(100vw - 16px) !important; max-width:none; margin-top:8px !important; margin-bottom:8px; }
  :global(.version-history-dialog .el-dialog__header) { padding:12px 14px; }
  :global(.version-history-dialog .el-dialog__headerbtn) { top:9px; right:8px; }
  .dialog-heading { padding-right:38px; }
  .history-review-workbench { grid-template-columns:1fr; grid-template-rows:auto minmax(0,1fr); height:calc(100dvh - 65px); min-height:0; max-height:none; }
  .review-heading { min-height:58px; padding:10px 14px; }
  .review-heading__identity { gap:9px; }
  .review-heading__identity > strong { font-size:18px; }
  .review-content { padding:14px; }
  .plan-snapshot-overview { grid-template-columns:1fr; }
  .plan-snapshot-overview div { padding:10px 12px; border-top:1px solid #e3e9e6; border-left:0; }
  .plan-snapshot-overview div:first-child { border-top:0; }
  .restore-bar { align-items:stretch; flex-direction:column; gap:9px; padding:11px 14px; }
  .restore-bar > span,.restore-bar .el-button { width:100%; }
}
</style>
