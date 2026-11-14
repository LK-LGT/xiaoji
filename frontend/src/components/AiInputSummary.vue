<template>
  <section class="ai-input-context">
    <div class="ai-input-context__heading">
      <div>
        <h3>输入上下文</h3>
        <small>本次 AI 请求实际使用的业务资料快照</small>
      </div>
      <el-tag v-if="model.parsed" size="small" type="success" effect="plain">已解析</el-tag>
      <el-tag v-else size="small" type="warning" effect="plain">原始文本</el-tag>
    </div>

    <el-alert
      v-if="model.warnings.length"
      class="ai-input-warning"
      type="warning"
      :closable="false"
      show-icon
      :title="`发现 ${model.warnings.length} 项需要关注`"
      :description="model.warnings.join('；')"
    />

    <template v-if="model.parsed">
      <section class="ai-input-overview">
        <h4>业务概览</h4>
        <dl class="ai-input-overview__grid">
          <div><dt>员工</dt><dd>{{ model.overview.employee }}</dd></div>
          <div><dt>部门</dt><dd>{{ model.overview.department }}</dd></div>
          <div><dt>计划周期</dt><dd>{{ model.overview.period }}</dd></div>
          <div class="span-two"><dt>员工意图</dt><dd>{{ model.overview.intent }}</dd></div>
          <div>
            <dt>当前表单</dt>
            <dd>{{ model.overview.currentFormItemCount }} 项<small v-if="model.overview.emptyFormItemCount">空白 {{ model.overview.emptyFormItemCount }} 项</small></dd>
          </div>
          <div v-if="model.sections.relatedWeekItems">
            <dt>本周已有任务</dt>
            <dd>{{ model.overview.relatedWeekItemCount }} 项</dd>
          </div>
        </dl>
      </section>

      <el-collapse class="ai-input-collapse">
        <el-collapse-item v-if="model.sections.parentOptions" name="parents">
          <template #title>
            <span class="ai-input-collapse__title"><strong>父级月计划</strong><small>{{ parentOptionCount }} 项候选</small></span>
          </template>
          <p class="ai-input-explanation">模型从已审批的月计划事项中选择周计划的父级任务。</p>
          <div v-if="model.parentGroups.length" class="ai-input-parent-groups">
            <section v-for="group in model.parentGroups" :key="group.planMonth" class="ai-input-parent-group">
              <header>
                <strong>{{ group.planMonth }}</strong>
                <span>{{ group.items.length }} 项</span>
                <el-tag v-if="group.periodFlag" size="small" :type="group.periodFlag === '未来周期' ? 'warning' : 'info'">{{ group.periodFlag }}</el-tag>
              </header>
              <div class="ai-input-item-list">
                <article v-for="item in group.items" :key="`${group.planMonth}-${item.id}`" class="ai-input-item">
                  <div class="ai-input-item__title"><strong>{{ item.taskName }}</strong><span>#{{ item.id }}</span></div>
                  <dl>
                    <div><dt>任务内容</dt><dd>{{ item.taskContent }}</dd></div>
                    <div><dt>交付物</dt><dd>{{ item.deliverable }}</dd></div>
                  </dl>
                </article>
              </div>
            </section>
          </div>
          <p v-else class="ai-input-empty">未提供父级月计划候选。</p>
        </el-collapse-item>

        <el-collapse-item v-if="model.sections.relatedWeekItems" name="related">
          <template #title>
            <span class="ai-input-collapse__title"><strong>本周已有任务</strong><small>{{ model.relatedWeekItems.length }} 项</small></span>
          </template>
          <p class="ai-input-explanation">用于避免重复生成，并帮助模型延续本周已经安排的工作。</p>
          <div v-if="model.relatedWeekItems.length" class="ai-input-item-list">
            <article v-for="item in model.relatedWeekItems" :key="item.id" class="ai-input-item">
              <div class="ai-input-item__title"><strong>{{ item.content }}</strong><span>#{{ item.id }}</span></div>
              <dl>
                <div><dt>父级任务</dt><dd>#{{ item.monthPlanItemId }}</dd></div>
                <div><dt>交付物</dt><dd>{{ item.deliverable }}</dd></div>
                <div><dt>质量标准</dt><dd>{{ item.quality }}</dd></div>
                <div><dt>完成日期</dt><dd>{{ item.plannedFinishDate }}</dd></div>
              </dl>
            </article>
          </div>
          <p v-else class="ai-input-empty">本周暂无已有任务。</p>
        </el-collapse-item>

        <el-collapse-item v-if="model.sections.knowledge" name="knowledge">
          <template #title>
            <span class="ai-input-collapse__title"><strong>企业知识</strong><small>{{ model.knowledgeItems.length }} 个片段</small></span>
          </template>
          <p class="ai-input-explanation">系统检索后提供给模型的企业规范和参考资料，不等同于员工输入。</p>
          <div v-if="model.knowledgeItems.length" class="ai-input-item-list">
            <article v-for="(item, index) in model.knowledgeItems" :key="`${item.knowledgeId}-${item.version}-${index}`" class="ai-input-item ai-input-knowledge">
              <div class="ai-input-item__title">
                <strong>{{ item.title }}</strong>
                <span>{{ item.knowledgeId }}@{{ item.version }}</span>
              </div>
              <div class="ai-input-tags">
                <el-tag size="small" effect="plain">{{ knowledgeTypeLabel(item.knowledgeType) }}</el-tag>
                <el-tag size="small" effect="plain" :type="bindingType(item.binding)">{{ item.bindingLabel }}</el-tag>
              </div>
              <p class="ai-input-citation">{{ item.citation }}</p>
              <details>
                <summary>查看知识正文</summary>
                <pre>{{ item.content }}</pre>
              </details>
            </article>
          </div>
          <p v-else class="ai-input-empty">未召回企业知识。</p>
        </el-collapse-item>

        <el-collapse-item v-if="model.sections.memory" name="memory">
          <template #title>
            <span class="ai-input-collapse__title"><strong>会话记忆</strong><small>{{ model.memory.recentMessages.length }} 条最近消息</small></span>
          </template>
          <p class="ai-input-explanation">只用于当前计划会话，帮助模型理解连续追问和修改要求。</p>
          <p v-if="!model.memory.hasHistory" class="ai-input-empty">本次为无历史上下文的新会话。</p>
          <dl v-else class="ai-input-detail-list">
            <div><dt>会话摘要</dt><dd>{{ model.memory.summary || '未生成摘要' }}</dd></div>
            <div><dt>最近消息</dt><dd><pre>{{ formatJson(model.memory.recentMessages) }}</pre></dd></div>
          </dl>
          <dl v-if="model.memory.instruction" class="ai-input-detail-list">
            <div><dt>记忆使用规则</dt><dd>{{ model.memory.instruction }}</dd></div>
          </dl>
        </el-collapse-item>

        <el-collapse-item v-if="model.sections.rules" name="rules">
          <template #title><span class="ai-input-collapse__title"><strong>生成规则</strong><small>上下文使用顺序</small></span></template>
          <p class="ai-input-explanation">当不同来源发生冲突时，模型应按以下顺序使用信息。</p>
          <ol v-if="model.priorityLabels.length" class="ai-input-priority">
            <li v-for="(label, index) in model.priorityLabels" :key="`${label}-${index}`"><span>{{ index + 1 }}</span>{{ label }}</li>
          </ol>
          <p v-else class="ai-input-empty">未记录上下文优先级。</p>
          <dl v-if="model.knowledgeUsageNotice" class="ai-input-detail-list">
            <div><dt>知识使用规则</dt><dd>{{ model.knowledgeUsageNotice }}</dd></div>
          </dl>
        </el-collapse-item>

        <el-collapse-item name="raw">
          <template #title><span class="ai-input-collapse__title"><strong>原始输入 JSON</strong><small>技术排查</small></span></template>
          <pre class="ai-input-raw">{{ model.prettyRawText }}</pre>
        </el-collapse-item>
      </el-collapse>
    </template>

    <section v-else class="ai-input-fallback">
      <h4>原始输入内容</h4>
      <p>输入 JSON 无法解析或疑似被截断，以下内容按日志原样保留。</p>
      <pre class="ai-input-raw">{{ model.prettyRawText }}</pre>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { parseAiInputSummary } from '@/utils/aiInputSummary'

type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger'

const props = defineProps<{ value: unknown }>()
const model = computed(() => parseAiInputSummary(props.value))
const parentOptionCount = computed(() => model.value.parentGroups.reduce((sum, group) => sum + group.items.length, 0))

const knowledgeTypes: Record<string, string> = {
  HISTORICAL_PLAN: '历史计划',
  PLAN_STANDARD: '计划规范',
  AI_GOVERNANCE: 'AI 治理',
  PROJECT_MATERIAL: '项目资料',
}

function knowledgeTypeLabel(value: string) {
  return knowledgeTypes[value] || value || '未标注类型'
}

function bindingType(value: string): TagType {
  if (value === 'NORMATIVE') return 'danger'
  if (value === 'REFERENCE') return 'primary'
  return 'info'
}

function formatJson(value: unknown) {
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}
</script>

<style scoped>
.ai-input-context { margin-top:20px; padding-top:18px; border-top:1px solid var(--line); }
.ai-input-context__heading { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:14px; }
.ai-input-context__heading h3, .ai-input-overview h4, .ai-input-fallback h4 { margin:0; color:var(--ink); font-size:14px; }
.ai-input-context__heading small { display:block; margin-top:5px; color:var(--muted); font-size:11px; line-height:1.5; }
.ai-input-warning { margin-bottom:14px; }
.ai-input-warning :deep(.el-alert__description) { line-height:1.6; }
.ai-input-overview { padding:14px 0 4px; }
.ai-input-overview h4 { margin-bottom:8px; }
.ai-input-overview__grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:0 22px; margin:0; }
.ai-input-overview__grid > div { min-width:0; padding:11px 0; border-bottom:1px solid #e7ebe5; }
.ai-input-overview__grid .span-two { grid-column:1 / -1; }
.ai-input-overview dt, .ai-input-detail-list dt, .ai-input-item dt { color:var(--muted); font-size:11px; }
.ai-input-overview dd { margin:5px 0 0; overflow-wrap:anywhere; color:var(--ink); font-size:13px; font-weight:650; line-height:1.55; }
.ai-input-overview dd small { display:inline; margin-left:8px; color:#b45309; font-size:11px; }
.ai-input-collapse { margin-top:12px; border-top:1px solid var(--line); }
.ai-input-collapse__title { display:flex; align-items:center; gap:9px; min-width:0; }
.ai-input-collapse__title strong { color:var(--ink); font-size:13px; }
.ai-input-collapse__title small { color:var(--muted); font-size:11px; }
.ai-input-explanation { margin:0 0 12px; color:var(--muted); font-size:12px; line-height:1.6; }
.ai-input-parent-groups { display:grid; gap:18px; padding-bottom:4px; }
.ai-input-parent-group > header { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
.ai-input-parent-group > header strong { color:var(--ink); font-size:13px; }
.ai-input-parent-group > header span { color:var(--muted); font-size:11px; }
.ai-input-item-list { display:grid; gap:8px; }
.ai-input-item { padding:11px 12px; border:1px solid var(--line); border-radius:6px; background:#fafbf8; }
.ai-input-item__title { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.ai-input-item__title strong { color:var(--ink); font-size:12px; line-height:1.55; }
.ai-input-item__title span { flex:none; color:var(--muted); font:10px/1.5 "IBM Plex Mono", "Cascadia Mono", monospace; }
.ai-input-item dl, .ai-input-detail-list { display:grid; gap:8px; margin:9px 0 0; }
.ai-input-item dl > div, .ai-input-detail-list > div { min-width:0; }
.ai-input-item dd, .ai-input-detail-list dd { margin:3px 0 0; overflow-wrap:anywhere; color:#40534c; font-size:12px; line-height:1.6; }
.ai-input-tags { display:flex; flex-wrap:wrap; gap:6px; margin-top:9px; }
.ai-input-citation { margin:8px 0 0; color:#52665f; font-size:11px; line-height:1.55; }
.ai-input-knowledge details { margin-top:9px; }
.ai-input-knowledge summary { cursor:pointer; color:var(--blue); font-size:11px; font-weight:700; }
.ai-input-knowledge pre, .ai-input-detail-list pre, .ai-input-raw { max-height:320px; margin:8px 0 0; overflow:auto; padding:11px; border:1px solid var(--line); border-radius:6px; background:#f6f7f1; color:#273a34; font:11px/1.6 "IBM Plex Mono", "Cascadia Mono", monospace; white-space:pre-wrap; word-break:break-word; }
.ai-input-empty { margin:0; padding:12px 0; color:var(--muted); font-size:12px; }
.ai-input-priority { display:flex; flex-wrap:wrap; gap:8px; margin:0; padding:0; list-style:none; }
.ai-input-priority li { display:flex; align-items:center; gap:6px; color:#40534c; font-size:12px; }
.ai-input-priority li:not(:last-child)::after { content:">"; margin-left:2px; color:#9aa9a3; }
.ai-input-priority span { display:grid; width:20px; height:20px; place-items:center; border-radius:50%; background:#e8eeeb; color:#36574b; font-size:10px; font-weight:800; }
.ai-input-fallback { padding-top:4px; }
.ai-input-fallback p { margin:7px 0 10px; color:var(--muted); font-size:12px; line-height:1.6; }
@media (max-width: 700px) {
  .ai-input-overview__grid { grid-template-columns:1fr; }
  .ai-input-overview__grid .span-two { grid-column:auto; }
  .ai-input-priority { align-items:flex-start; flex-direction:column; }
  .ai-input-priority li::after { display:none; }
}
</style>
