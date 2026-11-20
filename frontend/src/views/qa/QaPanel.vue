<template>
  <section class="page-panel qa-page">
    <div class="page-header">
      <div>
        <span class="eyebrow">PERFORMANCE Q&amp;A</span>
        <h1 class="page-title">绩效问答</h1>
        <p class="page-subtitle">
          用自然语言查询你的绩效数据。回答中的分数与达成率一律来自系统规则引擎，AI 只负责解读，不产生也不修改任何数字。
        </p>
      </div>
    </div>

    <!-- 任务选择 -->
    <div class="qa-taskbar" v-loading="tasksLoading">
      <div class="qa-taskbar__field">
        <label for="qa-task-select">查询范围</label>
        <el-select
          id="qa-task-select"
          v-model="selectedTaskId"
          placeholder="选择要查询的考核任务"
          class="qa-taskbar__select"
          :disabled="!tasks.length"
          filterable
          @change="handleTaskChange"
        >
          <el-option v-for="task in tasks" :key="task.task_id" :value="task.task_id">
            <span class="qa-option">
              <strong>{{ task.period }}</strong>
              <span class="qa-option__name">{{ task.label }}</span>
              <em>{{ task.scope === 'department' ? '部门' : '本人' }}</em>
            </span>
          </el-option>
        </el-select>
      </div>
      <div class="qa-taskbar__meta" v-if="selectedTask">
        <el-tag size="small" :type="selectedTask.scope === 'department' ? 'warning' : 'success'">
          {{ selectedTask.scope === 'department' ? '部门汇总' : '本人数据' }}
        </el-tag>
        <el-tag v-if="!selectedTask.has_snapshot" size="small" type="info" effect="plain">
          未冻结配置
        </el-tag>
        <small>{{ selectedTask.allowed_tools.length }} 项数据源可用</small>
      </div>
    </div>

    <el-alert
      v-if="tasksLoaded && !tasks.length"
      type="info"
      :closable="false"
      show-icon
      title="当前账号没有可查询的考核任务。"
      description="问答范围由系统权限决定，与你在考核中的角色和管辖范围一致。如需调整请联系 HR 或系统管理员。"
    />

    <!-- 问答区 -->
    <div v-else class="qa-conversation">
      <!-- 引导语 -->
      <div v-if="!messages.length" class="qa-guide">
        <div class="qa-guide__head">
          <span class="qa-guide__brand" aria-hidden="true">AI</span>
          <div>
            <h2>你可以这样问</h2>
            <p>提问范围限定在所选任务，且只读。回答会标注数据来源。</p>
          </div>
        </div>
        <div class="qa-guide__suggestions">
          <button
            v-for="item in suggestions"
            :key="item"
            type="button"
            class="qa-suggestion"
            :disabled="!selectedTaskId || asking"
            @click="ask(item)"
          >
            {{ item }}
          </button>
        </div>
        <ul class="qa-guide__notes">
          <li>AI 不会计算或修改分数 —— 所有数字都来自系统的规则引擎。</li>
          <li>数据缺失时 AI 会明确说明「查不到」，而不是猜测或估算。</li>
          <li>考勤异常仅作参考，不自动扣分，也不会计入算分依据。</li>
        </ul>
      </div>

      <!-- 消息列表 -->
      <div v-else class="qa-thread">
        <article v-for="(entry, index) in messages" :key="index" class="qa-entry" :class="`is-${entry.role}`">
          <div v-if="entry.role === 'user'" class="qa-entry__bubble">{{ entry.content }}</div>

          <div v-else class="qa-entry__answer">
            <div class="qa-entry__head">
              <span class="qa-entry__brand" aria-hidden="true">AI</span>
              <el-tag v-if="entry.answer?.degraded" size="small" type="warning">降级回答</el-tag>
              <el-tag v-else size="small" type="success">正常</el-tag>
              <small v-if="entry.answer?.data_quality?.as_of">
                数据截至 {{ formatTime(entry.answer.data_quality.as_of) }}
              </small>
            </div>

            <el-alert
              v-if="entry.answer?.degraded"
              class="qa-entry__degraded"
              type="warning"
              :closable="false"
              show-icon
              title="本次回答为降级结果"
              :description="entry.answer?.data_quality?.degrade_reason || 'AI 服务或部分数据源不可用，以下回答可能不完整，请以明细数据为准。'"
            />

            <!-- 回答正文（模型输出 Markdown） -->
            <div class="qa-entry__body" v-html="renderMarkdown(entry.content)"></div>

            <!-- 数据来源 -->
            <div v-if="entry.answer?.sources?.length" class="qa-entry__sources">
              <span class="qa-entry__sources-label">数据来源</span>
              <code v-for="source in entry.answer.sources" :key="source">{{ source }}</code>
            </div>

            <!-- 引用缺口提示（不是降级，但要让用户知道） -->
            <p v-if="entry.answer?.dropped_citations" class="qa-entry__citation-warning">
              本次回答有 {{ entry.answer.dropped_citations }} 处引用未能核验，已从来源标注中剔除。请以「系统查询明细」为准。
            </p>

            <!-- 工具调用留痕 -->
            <details v-if="entry.answer?.tool_calls?.length" class="qa-entry__tools">
              <summary>工具调用 {{ entry.answer.tool_calls.length }} 次</summary>
              <ul>
                <li v-for="(call, i) in entry.answer.tool_calls" :key="i" :class="{ 'is-failed': !call.ok }">
                  <code>{{ call.tool }}</code>
                  <span>{{ call.ok ? '成功' : `失败（${call.error}）` }}</span>
                  <small>{{ call.latency_ms }}ms</small>
                </li>
              </ul>
            </details>

            <!-- 预取的结构化数据（AI 不可用时用户仍能看明细） -->
            <details v-if="hasStructuredData(entry.answer)" class="qa-entry__data">
              <summary>查看系统查询明细</summary>
              <div v-for="(value, key) in entry.answer!.data" :key="key" class="qa-entry__data-block">
                <h4>{{ toolLabel(key) }}</h4>
                <pre>{{ formatJson(value) }}</pre>
              </div>
            </details>
          </div>
        </article>

        <div v-if="asking" class="qa-entry is-ai">
          <div class="qa-entry__answer">
            <div class="qa-entry__head">
              <span class="qa-entry__brand" aria-hidden="true">AI</span>
              <small>正在查询数据并组织回答…</small>
            </div>
            <el-skeleton :rows="3" animated />
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div v-if="selectedTaskId" class="qa-inputbar">
        <el-input
          v-model="question"
          type="textarea"
          :rows="2"
          maxlength="500"
          show-word-limit
          resize="none"
          placeholder="描述你想查的内容，例如：我这个月各项指标达成率如何？"
          @keydown.enter.exact.prevent="submit"
        />
        <div class="qa-inputbar__actions">
          <small>AI 只解读系统数据，不产生或修改任何数字</small>
          <el-button type="primary" :loading="asking" :disabled="!question.trim()" @click="submit">
            提问
          </el-button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { isValidQaAnswer, qaApi, type QaAnswer, type QaTask } from '@/api/qa'

/** 一条对话记录。 */
interface QaEntry {
  role: 'user' | 'ai'
  content: string
  answer?: QaAnswer
}

const tasks = ref<QaTask[]>([])
const tasksLoading = ref(false)
const tasksLoaded = ref(false)
const selectedTaskId = ref('')
const question = ref('')
const asking = ref(false)
const messages = ref<QaEntry[]>([])

const selectedTask = computed(() => tasks.value.find((t) => t.task_id === selectedTaskId.value) || null)

/**
 * 建议问题 —— 按数据范围给不同的引导。
 *
 * ⚠️ 部门范围只能问聚合类问题，不能问「我的分数」——
 *    问错了模型会返回"查不到"，不如不给这个按钮。
 */
const suggestions = computed(() => {
  if (selectedTask.value?.scope === 'department') {
    return [
      '本部门本期平均分是多少？',
      '部门内各等级人数分布如何？',
      '本期绩效数据的整体情况怎么样？',
    ]
  }
  return [
    '我这个月绩效是多少分？各项指标达成率如何？',
    '我为什么是这个等级？评分规则是怎么算的？',
    '我的哪些指标做得好？哪些需要改进？',
    '我的证据材料够充分吗？',
  ]
})

onMounted(async () => {
  tasksLoading.value = true
  try {
    const result = await qaApi.tasks()
    tasks.value = (result.items || []).map((task) => ({
      ...task,
      // ⚠️ 防御性兜底：`label` 是后端新加的字段。
      //    灰度期间可能连到还没上线的旧后端 → 至少要能显示名字，
      //    不能渲染成空白选项（用户会以为页面坏了）。
      label: task.label || task.name,
    }))
    // 默认选最近一个任务（后端已按创建时间倒序）
    if (tasks.value.length) selectedTaskId.value = tasks.value[0].task_id
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载可查询任务失败')
  } finally {
    tasksLoading.value = false
    tasksLoaded.value = true
  }
})

function handleTaskChange() {
  // 换任务 = 换数据范围，旧回答不再对应，清空避免误解
  messages.value = []
  question.value = ''
}

async function ask(text: string) {
  const trimmed = (text || question.value).trim()
  if (!trimmed || asking.value || !selectedTaskId.value) return

  messages.value.push({ role: 'user', content: trimmed })
  question.value = ''
  asking.value = true

  try {
    const answer = await qaApi.ask(trimmed, selectedTaskId.value, selectedTask.value?.period)
    if (!isValidQaAnswer(answer)) {
      // 引用不合法 —— 不渲染这份回答，避免把不可信内容当结论
      messages.value.push({
        role: 'ai',
        content: 'AI 返回的回答未通过引用校验，已丢弃。请稍后重试，或到汇总报表查看明细。',
      })
      return
    }
    messages.value.push({ role: 'ai', content: answer.answer, answer })
  } catch (error) {
    messages.value.push({
      role: 'ai',
      content: `提问失败：${error instanceof Error ? error.message : '服务暂不可用'}。请稍后重试。`,
    })
  } finally {
    asking.value = false
  }
}

function submit() {
  void ask(question.value)
}

function hasStructuredData(answer?: QaAnswer) {
  if (!answer?.data) return false
  return Object.keys(answer.data).some((key) => {
    const value = answer.data[key]
    if (!value || typeof value !== 'object') return false
    if (value.available === false) return false
    return Object.keys(value).length > 0
  })
}

function toolLabel(key: string) {
  return ({
    get_metric_values: '指标与等级规则',
    get_evidence: '证据材料',
    get_department_stats: '部门绩效汇总',
  } as Record<string, string>)[key] || key
}

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}

function formatTime(value: string) {
  return value.replace('T', ' ').slice(0, 16)
}

/**
 * 把 Markdown 转成 HTML。
 *
 * ⚠️ 为什么**不用** v-html 渲染原始 AI 输出：
 * AI 输出是不可信内容，直接 v-html 等于给了它 XSS 的口子
 * （比如回答里塞 `<img onerror=...>` 或 `<script>`）。
 *
 * 这里做的是**受限转换**：只处理标题、列表、表格、强调、代码，
 * 全部转义后再拼标签 —— 不保留任何原始 HTML。
 */
function escapeHtml(text: string) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderMarkdown(text: string) {
  const safe = escapeHtml(text || '')
  const lines = safe.split('\n')
  const out: string[] = []
  let inList = false
  let inTable = false

  const closeList = () => {
    if (inList) { out.push('</ul>'); inList = false }
  }
  const closeTable = () => {
    if (inTable) { out.push('</tbody></table>'); inTable = false }
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    const trimmed = line.trim()

    // 表格行
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      const cells = trimmed.slice(1, -1).split('|').map((c) => c.trim())
      // 跳过 |---|---| 分隔行
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue
      if (!inTable) {
        closeList()
        out.push('<table><thead><tr>')
        cells.forEach((c) => out.push(`<th>${inline(c)}</th>`))
        out.push('</tr></thead><tbody>')
        inTable = true
      } else {
        out.push('<tr>')
        cells.forEach((c) => out.push(`<td>${inline(c)}</td>`))
        out.push('</tr>')
      }
      continue
    }
    closeTable()

    // 列表
    if (/^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)) {
      if (!inList) { closeList(); out.push('<ul>'); inList = true }
      out.push(`<li>${inline(trimmed.replace(/^([-*]|\d+\.)\s+/, ''))}</li>`)
      continue
    }
    closeList()

    if (!trimmed) { out.push('<p class="qa-p-gap"></p>'); continue }

    // 标题
    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6)
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`)
      continue
    }

    // 引用
    if (trimmed.startsWith('&gt;')) {
      out.push(`<blockquote>${inline(trimmed.replace(/^&gt;\s*/, ''))}</blockquote>`)
      continue
    }

    out.push(`<p>${inline(trimmed)}</p>`)
  }
  closeList()
  closeTable()
  return out.join('')
}

/** 行内格式：粗体、代码、斜体。输入已转义。 */
function inline(text: string) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
}
</script>

<style scoped>
.qa-page { display: flex; flex-direction: column; gap: 18px; }

.qa-taskbar {
  display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 14px;
  padding: 14px 16px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface);
}
.qa-taskbar__field { display: grid; gap: 6px; min-width: 0; }
.qa-taskbar__field label { color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .08em; }
.qa-taskbar__select { width: min(460px, 100%); }
.qa-taskbar__meta { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; }
.qa-taskbar__meta small { color: var(--muted); font-size: 11px; }
.qa-option { display: inline-flex; align-items: baseline; gap: 8px; }
/* 同名任务靠后缀区分 —— 名字部分不该被后缀压得看不清 */
.qa-option__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qa-option strong { color: var(--navy-800); font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 12px; }
.qa-option em { color: var(--muted); font-size: 11px; font-style: normal; }

.qa-conversation { display: flex; flex-direction: column; gap: 16px; }

.qa-guide { padding: 22px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.qa-guide__head { display: flex; align-items: center; gap: 13px; margin-bottom: 16px; }
.qa-guide__brand, .qa-entry__brand {
  display: grid; place-items: center; border-radius: 8px; color: #fff; background: var(--navy-800);
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-weight: 800;
}
.qa-guide__brand { width: 34px; height: 34px; font-size: 12px; }
.qa-guide__head h2 { margin: 0; color: var(--ink); font-size: 16px; }
.qa-guide__head p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }

.qa-guide__suggestions { display: flex; flex-wrap: wrap; gap: 9px; margin-bottom: 16px; }
.qa-suggestion {
  padding: 9px 13px; border: 1px solid #cfd9d3; border-radius: 9px; color: #40534c; background: #f8f9f5;
  font-size: 12px; text-align: left; cursor: pointer; transition: border-color .18s ease, color .18s ease, background-color .18s ease;
}
.qa-suggestion:hover:not(:disabled) { border-color: var(--blue); color: var(--navy-800); background: var(--soft-blue); }
.qa-suggestion:disabled { opacity: .5; cursor: not-allowed; }

.qa-guide__notes { display: grid; gap: 6px; margin: 0; padding: 0 0 0 18px; color: var(--muted); font-size: 11px; line-height: 1.65; }

.qa-thread { display: grid; gap: 16px; }
.qa-entry { display: flex; flex-direction: column; }
.qa-entry.is-user { align-items: flex-end; }
.qa-entry__bubble {
  max-width: min(680px, 86%); padding: 10px 14px; border-radius: 12px 12px 3px 12px;
  color: #fff; background: var(--navy-800); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere;
}
.qa-entry__answer {
  width: 100%; padding: 16px 18px; border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--surface); box-shadow: var(--shadow-soft);
}
.qa-entry__head { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; margin-bottom: 12px; }
.qa-entry__brand { width: 26px; height: 26px; font-size: 10px; }
.qa-entry__head small { color: var(--muted); font-size: 11px; }
.qa-entry__degraded { margin-bottom: 12px; }

.qa-entry__body { color: #334742; font-size: 13px; line-height: 1.75; overflow-wrap: anywhere; }
.qa-entry__body :deep(h3), .qa-entry__body :deep(h4), .qa-entry__body :deep(h5), .qa-entry__body :deep(h6) {
  margin: 16px 0 7px; color: var(--ink); font-size: 14px; font-weight: 700;
}
.qa-entry__body :deep(h3:first-child), .qa-entry__body :deep(h4:first-child) { margin-top: 0; }
.qa-entry__body :deep(p) { margin: 7px 0; }
.qa-entry__body :deep(.qa-p-gap) { height: 4px; margin: 0; }
.qa-entry__body :deep(ul) { margin: 7px 0; padding-left: 20px; }
.qa-entry__body :deep(li) { margin: 3px 0; }
.qa-entry__body :deep(strong) { color: var(--ink); font-weight: 700; }
.qa-entry__body :deep(code) {
  padding: 1px 5px; border-radius: 4px; color: #4d655d; background: #f0f3ec;
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 11.5px;
}
.qa-entry__body :deep(blockquote) {
  margin: 9px 0; padding: 8px 13px; border-left: 3px solid #cfd9d3; color: #5e716c; background: #f8f9f5;
}
.qa-entry__body :deep(table) { width: 100%; margin: 11px 0; border-collapse: collapse; font-size: 12px; }
.qa-entry__body :deep(th), .qa-entry__body :deep(td) {
  padding: 7px 10px; border: 1px solid #e0e6e1; text-align: left; vertical-align: top;
}
.qa-entry__body :deep(th) { color: var(--ink); background: #f4f7f3; font-weight: 700; }

.qa-entry__sources { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-top: 13px; padding-top: 11px; border-top: 1px solid #e7ebe5; }
.qa-entry__sources-label { color: var(--muted); font-size: 11px; font-weight: 700; }
.qa-entry__sources code {
  padding: 2px 6px; border: 1px solid #dde4df; border-radius: 4px; color: #52645f; background: #f8f9f5;
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 10.5px;
}
.qa-entry__citation-warning {
  margin: 9px 0 0; padding: 8px 11px; border-left: 3px solid #d8a04a; border-radius: 0 6px 6px 0;
  color: #6d5a2c; background: #fdf8ec; font-size: 11.5px; line-height: 1.6;
}

.qa-entry__tools, .qa-entry__data { margin-top: 11px; padding-top: 10px; border-top: 1px solid #e7ebe5; }
.qa-entry__tools summary, .qa-entry__data summary { color: var(--blue); font-size: 11.5px; font-weight: 700; cursor: pointer; }
.qa-entry__tools ul { margin: 9px 0 0; padding: 0; list-style: none; }
.qa-entry__tools li { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; padding: 4px 0; color: #52645f; font-size: 11px; }
.qa-entry__tools li.is-failed { color: #b4473b; }
.qa-entry__tools li code { font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 11px; }
.qa-entry__tools li small { margin-left: auto; color: var(--muted); font-size: 10px; }
.qa-entry__data-block { margin-top: 11px; }
.qa-entry__data-block h4 { margin: 0 0 5px; color: var(--ink); font-size: 12px; }
.qa-entry__data-block pre {
  max-height: 320px; margin: 0; overflow: auto; padding: 10px 12px;
  border: 1px solid var(--line); border-radius: 8px; background: #f6f7f1; color: #273a34;
  font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 11px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
}

.qa-inputbar {
  position: sticky; bottom: 0; display: grid; gap: 9px; padding: 13px 15px;
  border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow);
}
.qa-inputbar__actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.qa-inputbar__actions small { color: var(--muted); font-size: 11px; }

@media (max-width: 720px) {
  .qa-taskbar { align-items: stretch; flex-direction: column; }
  .qa-taskbar__select { width: 100%; }
  .qa-inputbar__actions { align-items: stretch; flex-direction: column; }
}
</style>
