<template>
  <section v-loading="loading" class="page-panel week-plan-detail">
    <div v-if="isLeaderWorkspace" class="page-header">
      <div><h1 class="page-title">周计划详情</h1><p class="page-subtitle">查看本周工作内容、统计周期和审批反馈。</p></div>
      <div class="toolbar"><el-button @click="backToList">返回列表</el-button><el-button v-if="detail?.summary.status === 'PENDING'" type="warning" plain :loading="withdrawing" @click="withdrawPlan">撤回计划</el-button><el-button v-if="editable" type="primary" @click="router.push(`${weekPlanBasePath}/${id}/edit`)">继续编辑</el-button></div>
    </div>
    <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />
    <template v-if="detail">
      <section class="week-detail-hero mt16">
        <div><span>自然周</span><strong>{{ detail.summary.weekStart }} — {{ detail.summary.weekEnd }}</strong><p>{{ detail.summary.title }}</p></div>
        <dl><div><dt>工作要点</dt><dd>{{ workPointCount }}</dd></div><div><dt>下级日计划</dt><dd>{{ detail.dayPlanCount }}</dd></div></dl>
        <div class="week-detail-hero__status"><el-tag :type="weekPlanStatusMeta[detail.summary.status].type" size="large">{{ weekPlanStatusMeta[detail.summary.status].label }}</el-tag><PlanAiReviewCell :review="aiReviews.reviewFor(id)" :loading="aiReviews.loading.value" @view="aiReviewDrawerVisible = true" /><div v-if="!isLeaderWorkspace" class="toolbar"><el-button @click="backToList">返回列表</el-button><el-button v-if="detail.summary.status === 'PENDING'" type="warning" plain :loading="withdrawing" @click="withdrawPlan">撤回计划</el-button><el-button v-if="editable" type="primary" @click="router.push(`${weekPlanBasePath}/${id}/edit`)">继续编辑</el-button></div></div>
      </section>
      <PlanFeedbackBanner class="mt16" :status="detail.summary.status" :comment="detail.summary.approvalComment" :next-step="editable ? '根据意见修改计划并重新提交' : undefined" />
      <section class="week-detail-stage mt16">
        <div class="section-header"><div><h2>周计划表</h2><p>所有工作要点统一汇总在同一行。</p></div></div>
        <div class="week-detail-table-wrap">
          <table class="week-detail-table">
            <thead><tr><th class="name">姓名</th><th class="content">工作内容</th><th class="standard">考核标准（交付物、质量）</th><th class="period">统计周期</th></tr></thead>
            <tbody><tr><td class="center">{{ detail.summary.employeeName }}</td><td><p>{{ mergedFields.content }}</p></td><td><div class="week-standard-cell"><p><strong>交付物：</strong>{{ mergedFields.deliverable || '未填写' }}</p><p><strong>质量：</strong>{{ mergedFields.quality || '未填写' }}</p></div></td><td class="center">{{ detail.summary.weekStart }} 至 {{ detail.summary.weekEnd }}</td></tr></tbody>
          </table>
        </div>
        <div v-if="resultSubmissionAvailable && detail.items[0]" class="week-detail-actions"><el-button type="primary" plain @click="openResultSubmit(detail.items[0])">提交成果</el-button></div>
      </section>
    </template>
    <PlanAiReviewDrawer v-model="aiReviewDrawerVisible" plan-type="WEEK" :review="aiReviews.reviewFor(id)" :loading="aiReviews.loading.value" :subject="detail ? `${detail.summary.weekStart} 至 ${detail.summary.weekEnd} 周计划` : ''" :context="detail?.summary.employeeName || ''" />
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { getEmployeeWeekPlanApi, weekPlanStatusMeta, withdrawEmployeeWeekPlanApi, type WeekPlanDetail, type WeekPlanItem } from '@/api/weekPlan'
import PlanFeedbackBanner from './components/PlanFeedbackBanner.vue'
import { useSmartBack } from '@/composables/useSmartBack'
import { countWeekWorkPoints, mergeWeekPlanFields } from '@/utils/weekPlanDisplay'
import { periodMonthKey } from '@/utils/period'
import PlanAiReviewCell from '@/components/PlanAiReviewCell.vue'
import PlanAiReviewDrawer from '@/components/PlanAiReviewDrawer.vue'
import { useAiReviewCollection } from '@/composables/useAiReviewCollection'
const route = useRoute(); const router = useRouter(); // BUG-R6-14：周计划主键是字符串 UUID，禁止 `Number(...)`。
const id = String(route.params.id ?? '').trim(); const loading = ref(false); const errorMessage = ref(''); const detail = ref<WeekPlanDetail | null>(null)
const withdrawing = ref(false)
const aiReviews = useAiReviewCollection('WEEK_PLAN')
const aiReviewDrawerVisible = ref(false)
const isLeaderWorkspace = route.path.startsWith('/leader/')
const weekPlanBasePath = isLeaderWorkspace ? '/leader/my-week-plans' : '/employee/week-plans'
const { goBack } = useSmartBack(weekPlanBasePath)
const editable = computed(() => detail.value?.summary.status === 'DRAFT' || detail.value?.summary.status === 'REJECTED')
const resultSubmissionAvailable = computed(() => detail.value?.summary.status === 'APPROVED')
const mergedFields = computed(() => mergeWeekPlanFields(detail.value?.items || []))
const workPointCount = computed(() => countWeekWorkPoints(mergedFields.value.content))
function backToList() {
  void goBack(weekPlanBasePath)
}
function openResultSubmit(item: WeekPlanItem) {
  // ⚠️ **BUG-R6-26**：统一走 `periodMonthKey`（不再手写 `slice(0, 7)`）。
  const month = periodMonthKey(detail.value?.summary.weekStart)
  if (isLeaderWorkspace) {
    void router.push({ path: '/leader/results/submit', query: { planType: 'WEEK', weekMonth: month, weekPlanId: String(id), weekPlanItemId: String(item.id) } })
    return
  }
  void router.push({ path: `/employee/results/workspace/WEEK/${id}`, query: { month, taskId: String(item.id) } })
}
async function withdrawPlan() {
  const summary = detail.value?.summary
  if (!summary || summary.status !== 'PENDING' || withdrawing.value) return
  try {
    await ElMessageBox.confirm('撤回后周计划恢复为草稿，可修改后重新提交。确认撤回？', '撤回周计划', {
      type: 'warning',
      confirmButtonText: '确认撤回',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  withdrawing.value = true
  errorMessage.value = ''
  try {
    await withdrawEmployeeWeekPlanApi(summary.id, summary.versionNo)
    ElMessage.success('周计划已撤回为草稿')
    await router.replace(`${weekPlanBasePath}/${summary.id}/edit`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '撤回周计划失败，请稍后重试'
    ElMessage.error(errorMessage.value)
  } finally {
    withdrawing.value = false
  }
}
onMounted(async () => { loading.value = true; try { const [plan] = await Promise.all([getEmployeeWeekPlanApi(id), aiReviews.loadOne(id)]); detail.value = plan } catch (error) { errorMessage.value = error instanceof Error ? error.message : '详情加载失败' } finally { loading.value = false } })
</script>
<style scoped>
.week-detail-hero { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 24px; align-items: center; padding: 22px; border: 1px solid #bfd4c9; border-radius: 13px; background: linear-gradient(110deg, #eef7f2, #fffdf8 70%); }.week-detail-hero > div { display: grid; gap: 5px; }.week-detail-hero > div span { color: var(--blue); font-size: 10px; font-weight: 800; letter-spacing: .1em; }.week-detail-hero > div strong { font-size: 22px; }.week-detail-hero p { margin: 0; color: var(--muted); }.week-detail-hero dl { display: flex; margin: 0; }.week-detail-hero dl div { min-width: 90px; padding: 0 18px; border-left: 1px solid #cddbd4; }.week-detail-hero dt { color: var(--muted); font-size: 11px; }.week-detail-hero dd { margin: 3px 0 0; font-size: 22px; font-weight: 800; }
.week-detail-stage { padding:20px; border:1px solid var(--line); border-radius:12px; background:#fffdf8; }
.week-detail-table-wrap { width:100%; margin-top:16px; overflow-x:auto; border:1px solid #cfd9d3; }
.week-detail-table { width:100%; min-width:1020px; border-collapse:collapse; table-layout:fixed; background:#fff; }
.week-detail-table th,.week-detail-table td { padding:13px 11px; border-right:1px solid #cfd9d3; border-bottom:1px solid #d9e1dc; vertical-align:middle; line-height:1.65; overflow-wrap:anywhere; }
.week-detail-table th:last-child,.week-detail-table td:last-child { border-right:0; }
.week-detail-table th { color:#304a42; background:#e6f0f6; font-weight:750; text-align:center; }
.week-detail-table th.name { width:100px; }.week-detail-table th.content { width:360px; }.week-detail-table th.standard { width:300px; }.week-detail-table th.period { width:180px; }
.week-detail-table p { margin:0; white-space:pre-wrap; }.week-detail-table .center { text-align:center; }
.week-standard-cell { display:grid; gap:6px; }
.week-standard-cell strong { color:#304a42; }
.week-detail-actions { display:flex; justify-content:flex-end; margin-top:14px; }
.week-detail-hero__status { display:flex; align-items:flex-end; flex-direction:column; gap:7px; }
@media (max-width:900px) { .week-detail-hero { grid-template-columns:1fr; }.week-detail-hero dl div:first-child { border-left:0; padding-left:0; } }
</style>
