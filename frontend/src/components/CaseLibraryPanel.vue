/**
 * P2-3 案例库面板。
 *
 * 案例库是「参考做法」，不是「查某人的历史」。
 * 因此呈现上必须强调两点：
 * 1. **已脱敏** —— 不含员工身份与评语正文（后端强制，前端再标示一次）；
 * 2. **参考性质** —— 案例是别人的做法，不是本次的判定依据。
 */
<template>
  <section class="case-panel">
    <header class="panel-head">
      <div class="head-text">
        <span class="eyebrow">案例库</span>
        <h3 class="panel-title">{{ title }}</h3>
        <p class="panel-subtitle">已脱敏的定稿做法，供参考与培训</p>
      </div>
    </header>

    <div class="toolbar">
      <el-input
        v-model="keyword"
        placeholder="搜索标题或处理要点"
        style="width: 240px"
        clearable
        @keyup.enter="load"
      />
      <el-input
        v-model="scenario"
        placeholder="场景（如 low_score）"
        style="width: 200px"
        clearable
        @keyup.enter="load"
      />
      <el-button type="primary" :loading="loading" @click="load">查询</el-button>
      <el-button v-if="canPublish" @click="showPublish = true">收录案例</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="4" animated />

    <el-empty v-else-if="!cases.length" :image-size="80" description="暂无案例" />

    <div v-else class="case-list">
      <article v-for="item in cases" :key="item.id" class="case-item">
        <div class="case-head">
          <strong>{{ item.title }}</strong>
          <el-tag size="small" effect="plain">{{ item.scenario }}</el-tag>
          <el-tag v-if="item.rating" size="small" type="info" effect="dark">
            {{ item.rating }}
          </el-tag>
          <el-tag v-if="item.redacted" size="small" type="success" effect="plain">
            已脱敏
          </el-tag>
          <span class="use-count">被参考 {{ item.useCount }} 次</span>
        </div>

        <dl v-if="Object.keys(item.facts ?? {}).length" class="case-facts">
          <div v-if="item.facts.total !== undefined">
            <dt>总分</dt><dd>{{ item.facts.total }}</dd>
          </div>
          <div v-for="metric in item.facts.metrics ?? []" :key="metric.metric">
            <dt>{{ metric.metric }}</dt>
            <dd>
              达成率 {{ metric.ratio ?? '—' }} · 等级 {{ metric.level ?? '—' }}
            </dd>
          </div>
        </dl>

        <p class="case-resolution">{{ item.resolution }}</p>
      </article>
    </div>

    <!-- 收录对话框 -->
    <el-dialog v-model="showPublish" title="收录案例" width="min(560px, calc(100vw - 32px))">
      <el-form label-width="90px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="例如：销售岗低分处理" />
        </el-form-item>
        <el-form-item label="场景">
          <el-input v-model="form.scenario" placeholder="low_score" />
        </el-form-item>
        <el-form-item label="来源记录">
          <el-input v-model="form.sourceRecordId" placeholder="考核记录 ID（可选）" />
        </el-form-item>
        <el-form-item label="处理要点" required>
          <el-input
            v-model="form.resolution"
            type="textarea"
            :rows="4"
            placeholder="如何处理的？（只写做法，不写具体员工信息）"
          />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="入库即脱敏"
        description="案例只保存数值与结构化事实，不含姓名与评语正文。来源记录必须已确认评级。"
      />
      <template #footer>
        <el-button @click="showPublish = false">取消</el-button>
        <el-button type="primary" :loading="publishing" @click="publish">收录</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { feedbackApi, type CaseLibraryItem } from '@/api/p2'

const props = withDefaults(
  defineProps<{
    title?: string
    /** 是否允许收录（HR / 主管） */
    canPublish?: boolean
  }>(),
  { title: '案例库', canPublish: false },
)

const cases = ref<CaseLibraryItem[]>([])
const loading = ref(false)
const publishing = ref(false)
const showPublish = ref(false)
const keyword = ref('')
const scenario = ref('')

const form = reactive({
  title: '',
  scenario: 'general',
  resolution: '',
  sourceRecordId: '',
})

async function load() {
  loading.value = true
  try {
    cases.value = await feedbackApi.listCases({
      keyword: keyword.value || undefined,
      scenario: scenario.value || undefined,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '案例加载失败')
    cases.value = []
  } finally {
    loading.value = false
  }
}

async function publish() {
  if (!form.title || !form.resolution) {
    ElMessage.warning('标题与处理要点不能为空')
    return
  }
  publishing.value = true
  try {
    await feedbackApi.publishCase({
      title: form.title,
      scenario: form.scenario || 'general',
      resolution: form.resolution,
      source_record_id: form.sourceRecordId || null,
    })
    ElMessage.success('已收录（已脱敏）')
    showPublish.value = false
    form.title = ''
    form.resolution = ''
    form.sourceRecordId = ''
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '收录失败')
  } finally {
    publishing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.case-panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
}

.panel-head {
  margin-bottom: 12px;
}

.eyebrow {
  display: block;
  font-size: 11px;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 4px 0;
  color: #303133;
}

.panel-subtitle {
  font-size: 13px;
  color: #606266;
  margin: 0;
}

.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.case-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.case-item {
  border: 1px solid #ebeef5;
  border-radius: 5px;
  padding: 12px;
  background: #fafafa;
}

.case-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.case-head strong {
  font-size: 14px;
  color: #303133;
}

.use-count {
  font-size: 11px;
  color: #909399;
  margin-left: auto;
}

.case-facts {
  margin: 0 0 8px 0;
  font-size: 12px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.case-facts > div {
  display: flex;
  gap: 6px;
}

.case-facts dt {
  color: #909399;
}

.case-facts dd {
  margin: 0;
  color: #606266;
}

.case-resolution {
  font-size: 13px;
  color: #303133;
  margin: 0;
  line-height: 1.7;
}
</style>
