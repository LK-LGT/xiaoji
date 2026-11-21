<template>
  <div class="pan-workload-entry">
    <!-- ① 我的日汇总（近期） -->
    <section class="section">
      <h4 class="section-title">
        我的近期工作量
        <span class="section-hint">系统判定 / 裁定后 两个值都列出来</span>
      </h4>
      <el-table :data="dailies" size="small" border stripe v-loading="loading">
        <el-table-column prop="businessDate" label="日期" width="120" />
        <el-table-column label="系统判定" width="100" align="right">
          <template #default="{ row }">{{ row.totalCount }}</template>
        </el-table-column>
        <el-table-column label="裁定后" width="100" align="right">
          <template #default="{ row }">
            <b v-if="row.adjustedCount !== null">{{ row.adjustedCount }}</b>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="excludedCount" label="排除" width="80" align="right" />
        <el-table-column label="标记" min-width="140">
          <template #default="{ row }">
            <el-tag v-for="m in markerEntries(row)" :key="m.name" size="small" effect="plain" class="marker">
              {{ m.name }} {{ m.count }}
            </el-tag>
            <span v-if="!markerEntries(row).length" class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDispute(row)">提出异议</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">暂无数据 —— 先让 HR 配置目录绑定并跑一次采集</span>
        </template>
      </el-table>
    </section>

    <!-- ② 人工填报 -->
    <section class="section">
      <h4 class="section-title">
        人工填报
        <span class="section-hint">完播率 / 留存率</span>
      </h4>
      <el-form :inline="true" :model="metricForm" class="metric-form">
        <el-form-item label="日期">
          <el-date-picker
            v-model="metricForm.metric_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
          />
        </el-form-item>
        <el-form-item label="指标">
          <el-select v-model="metricForm.metric_code" style="width: 130px">
            <el-option
              v-for="(label, code) in MANUAL_METRIC_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="数值">
          <el-input-number
            v-model="metricForm.metric_value"
            :min="0"
            :max="1"
            :step="0.01"
            :precision="4"
            style="width: 120px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="submitMetric" :loading="submitting">提交</el-button>
        </el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" class="ratio-note">
        比率用 0~1 表示：35% 填 0.35。
      </el-alert>

      <el-table :data="metrics" size="small" border stripe class="metric-table">
        <el-table-column prop="metricDate" label="日期" width="120" />
        <el-table-column label="指标" width="120">
          <template #default="{ row }">
            {{ MANUAL_METRIC_LABELS[row.metricCode] ?? row.metricCode }}
          </template>
        </el-table-column>
        <el-table-column label="数值" width="100" align="right">
          <template #default="{ row }">{{ (row.metricValue * 100).toFixed(1) }}%</template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.source === 'feishu' ? 'success' : 'info'">
              {{ SOURCE_LABELS[row.source] ?? row.source }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="uploadEvidence(row)">传证据</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">还没有填报记录</span>
        </template>
      </el-table>
    </section>

    <!-- ③ 发起异议抽屉 -->
    <el-drawer v-model="disputeDrawer" title="提出异议" size="60%">
      <div v-if="disputeTarget" class="dispute-form">
        <el-alert type="warning" :closable="false" class="dispute-note">
          提交后由主管裁定。<b>裁定结果会写回日汇总</b>，全过程留痕可审计。
        </el-alert>

        <el-form :label-width="110">
          <el-form-item label="业务日期">
            <b>{{ disputeTarget.businessDate }}</b>
            <span class="muted">（系统判定 {{ disputeTarget.totalCount }} 条）</span>
          </el-form-item>
          <el-form-item label="裁定粒度">
            <el-radio-group v-model="disputeForm.scope">
              <el-radio value="day">按天整体裁定</el-radio>
              <el-radio value="file">逐文件裁定</el-radio>
            </el-radio-group>
            <div class="muted hint">
              「按天」= 主管直接定这天最终多少条；「逐文件」= 逐个争议文件单独判。
            </div>
          </el-form-item>
          <el-form-item label="我主张条数">
            <el-input-number v-model="disputeForm.claimed_count" :min="0" :max="10000" />
          </el-form-item>
          <el-form-item label="申诉理由">
            <el-input
              v-model="disputeForm.reason"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              placeholder="说明哪里不对"
            />
          </el-form-item>
        </el-form>

        <!-- 争议项 -->
        <template v-if="disputeForm.scope === 'file'">
          <h5 class="drawer-subtitle">争议项（可选）</h5>
          <div class="muted hint">
            勾选系统已列出的文件，或手填系统没扫到的（如漏传）。留空也可以只按天裁定。
          </div>
          <el-table :data="dayFiles" size="small" border class="file-picker" v-loading="filesLoading">
            <el-table-column width="50">
              <template #default="{ row }">
                <el-checkbox
                  :model-value="selectedFileIds.includes(row.id)"
                  @change="(v: boolean) => toggleFile(row.id, v)"
                />
              </template>
            </el-table-column>
            <el-table-column prop="fileName" label="文件名" min-width="180" show-overflow-tooltip />
            <el-table-column label="计入" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.counted ? 'success' : 'info'" effect="plain">
                  {{ row.counted ? '计入' : '排除' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="excludeReason" label="排除原因" width="130" show-overflow-tooltip />
          </el-table>

          <div class="manual-item">
            <el-input v-model="manualName" placeholder="系统没扫到的文件名（可留空）" />
            <el-select v-model="manualDisputeType" style="width: 130px">
              <el-option label="系统漏了" value="missing" />
              <el-option label="系统多算了" value="overcounted" />
              <el-option label="权重不对" value="weight" />
            </el-select>
            <el-button size="small" @click="addManualItem" :disabled="!manualName.trim()">添加</el-button>
          </div>
          <div v-if="manualItems.length" class="manual-item-list">
            <el-tag
              v-for="(m, i) in manualItems"
              :key="i"
              closable
              size="small"
              type="warning"
              effect="plain"
              @close="manualItems.splice(i, 1)"
            >
              {{ m.name }}（{{ DISPUTE_LABELS[m.type] }}）
            </el-tag>
          </div>
        </template>

        <!-- 证据 -->
        <h5 class="drawer-subtitle">证据截图（可选）</h5>
        <el-upload
          v-model:file-list="uploadFiles"
          action="#"
          :auto-upload="false"
          :limit="5"
          multiple
        >
          <el-button size="small">选择文件</el-button>
          <template #tip>
            <div class="muted hint">支持图片 / 视频 / 文档，单个 ≤ 20MB</div>
          </template>
        </el-upload>

        <div class="drawer-footer">
          <el-button @click="disputeDrawer = false">取消</el-button>
          <el-button type="primary" @click="submitDispute" :loading="submitting">提交异议</el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 隐藏的文件 input（证据上传）-->
    <input ref="evidenceInput" type="file" class="hidden-input" @change="onEvidencePicked" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, type UploadFile } from 'element-plus'
import {
  MANUAL_METRIC_LABELS,
  panWorkloadActions,
  panWorkloadApi,
  type DisputeType,
  type ManualMetricCode,
  type PanManualMetric,
  type PanWorkloadDaily,
  type PanWorkloadFile,
} from '@/api/panWorkload'

/** 争议类型中文名。 */
const DISPUTE_LABELS: Record<string, string> = {
  missing: '系统漏了',
  overcounted: '系统多算了',
  weight: '权重不对',
  other: '其他',
}

/** 来源中文名。 */
const SOURCE_LABELS: Record<string, string> = {
  frontend: '网页端',
  feishu: '飞书端',
  import: '导入',
}

const loading = ref(false)
const submitting = ref(false)
const dailies = ref<PanWorkloadDaily[]>([])
const metrics = ref<PanManualMetric[]>([])

const disputeDrawer = ref(false)
const disputeTarget = ref<PanWorkloadDaily | null>(null)
const dayFiles = ref<PanWorkloadFile[]>([])
const filesLoading = ref(false)
const selectedFileIds = ref<string[]>([])
const manualItems = ref<{ name: string; type: DisputeType }[]>([])
const manualName = ref('')
const manualDisputeType = ref<DisputeType>('missing')
const uploadFiles = ref<UploadFile[]>([])

const metricForm = reactive({
  metric_date: '',
  metric_code: 'video_completion_rate' as ManualMetricCode,
  metric_value: 0.35,
})

const disputeForm = reactive({
  scope: 'day' as 'day' | 'file',
  claimed_count: 0,
  reason: '',
})

const evidenceInput = ref<HTMLInputElement | null>(null)
const evidenceMetricId = ref('')

/** 加载我的数据。 */
async function loadAll() {
  loading.value = true
  try {
    const [daily, manual] = await Promise.all([
      panWorkloadApi.getDaily(),
      panWorkloadApi.getManualMetrics(),
    ])
    dailies.value = daily.items
    metrics.value = manual.items
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

/** 标记拆分。 */
function markerEntries(row: PanWorkloadDaily) {
  return Object.entries(row.markerBreakdown ?? {}).map(([name, count]) => ({
    name,
    count: Number(count),
  }))
}

/** 提交人工指标。 */
async function submitMetric() {
  if (!metricForm.metric_date) {
    ElMessage.warning('请选择日期')
    return
  }
  submitting.value = true
  try {
    await panWorkloadActions.submitManualMetric({
      metric_date: metricForm.metric_date,
      metric_code: metricForm.metric_code,
      metric_value: metricForm.metric_value,
    })
    ElMessage.success('已提交')
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

/** 打开异议抽屉。 */
async function openDispute(row: PanWorkloadDaily) {
  disputeTarget.value = row
  disputeForm.scope = 'day'
  disputeForm.claimed_count = row.totalCount
  disputeForm.reason = ''
  selectedFileIds.value = []
  manualItems.value = []
  uploadFiles.value = []
  await loadDayFiles(row)
  disputeDrawer.value = true
}

/** 加载那天的全部文件（含被排除的）。 */
async function loadDayFiles(row: PanWorkloadDaily) {
  filesLoading.value = true
  try {
    const res = await panWorkloadApi.getFiles({
      employee_id: row.employeeId,
      business_date: row.businessDate,
    })
    dayFiles.value = res.items
  } catch {
    dayFiles.value = []
  } finally {
    filesLoading.value = false
  }
}

/** 勾选/取消勾选文件。 */
function toggleFile(id: string, checked: boolean) {
  if (checked) {
    if (!selectedFileIds.value.includes(id)) selectedFileIds.value.push(id)
  } else {
    selectedFileIds.value = selectedFileIds.value.filter((x) => x !== id)
  }
}

/** 添加手填争议项。 */
function addManualItem() {
  const name = manualName.value.trim()
  if (!name) return
  manualItems.value.push({ name, type: manualDisputeType.value })
  manualName.value = ''
}

/** 提交异议。 */
async function submitDispute() {
  const target = disputeTarget.value
  if (!target) return
  if (!disputeForm.reason.trim()) {
    ElMessage.warning('请填写申诉理由')
    return
  }
  submitting.value = true
  try {
    const res = await panWorkloadActions.createReview({
      business_date: target.businessDate,
      claimed_count: disputeForm.claimed_count,
      reason: disputeForm.reason,
      scope: disputeForm.scope,
    })
    const reviewId = res.item.id

    // 争议项
    for (const fid of selectedFileIds.value) {
      await panWorkloadActions.addItem(reviewId, {
        pan_workload_file_id: fid,
        dispute_type: 'missing',
      })
    }
    for (const m of manualItems.value) {
      await panWorkloadActions.addItem(reviewId, {
        manual_name: m.name,
        dispute_type: m.type,
      })
    }

    // 证据
    for (const f of uploadFiles.value) {
      if (!f.raw) continue
      await panWorkloadActions.uploadArtifact(reviewId, f.raw as File)
    }

    ElMessage.success('已提交，等待主管裁定')
    disputeDrawer.value = false
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

/** 触发证据上传（隐藏 input）。 */
function uploadEvidence(row: PanManualMetric) {
  evidenceMetricId.value = row.id
  evidenceInput.value?.click()
}

/** 证据文件选中。 */
async function onEvidencePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !evidenceMetricId.value) return
  try {
    await panWorkloadActions.uploadMetricArtifact(evidenceMetricId.value, file)
    ElMessage.success('证据已上传')
  } catch (err: unknown) {
    ElMessage.error(err instanceof Error ? err.message : '上传失败')
  } finally {
    input.value = ''
  }
}

onMounted(loadAll)

defineExpose({ reload: loadAll })
</script>

<style scoped>
.pan-workload-entry {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-title {
  margin: 0;
  font-size: 14px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.section-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.marker {
  margin-right: 4px;
}

.metric-form {
  margin-bottom: 4px;
}

.ratio-note {
  margin-bottom: 8px;
}

.metric-table {
  margin-top: 8px;
}

.dispute-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dispute-note {
  margin-bottom: 4px;
}

.drawer-subtitle {
  margin: 8px 0 0;
  font-size: 13px;
}

.hint {
  margin-top: 4px;
  line-height: 1.6;
}

.file-picker {
  margin-top: 8px;
}

.manual-item {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.manual-item-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.hidden-input {
  display: none;
}
</style>
