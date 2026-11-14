<template>
  <section class="page-panel knowledge-ingest-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">文档入库</h1>
        <p class="page-subtitle">上传文档后自动完成内容解析、分段和向量写入。</p>
      </div>
    </div>

    <div class="ingest-layout">
      <section class="upload-pane" aria-labelledby="upload-title">
        <div class="section-header">
          <div>
            <h2 id="upload-title">上传文档</h2>
            <p>支持 Word 和 Markdown，单个文件不超过 10 MB。</p>
          </div>
        </div>

        <KnowledgeBatchUpload :orgs="orgTree" :upload-request="uploadKnowledgeFile" @uploaded="loadUploads" />
      </section>

      <section class="recent-pane" aria-labelledby="recent-title" v-loading="listLoading">
        <div class="recent-head">
          <div>
            <h2 id="recent-title">最近入库</h2>
            <p>共 {{ recentUploads.length }} 篇</p>
          </div>
          <el-tooltip content="刷新入库记录" placement="top">
            <el-button
              circle
              :icon="Refresh"
              :loading="listLoading"
              aria-label="刷新入库记录"
              @click="loadUploads"
            />
          </el-tooltip>
        </div>

        <div v-if="recentUploads.length" class="recent-list">
          <article v-for="item in recentUploads" :key="item.documentId">
            <el-icon><Document /></el-icon>
            <div class="recent-copy">
              <strong>{{ item.title }}</strong>
              <span>{{ item.fileName }}</span>
              <small>{{ item.chunkCount }} 个片段 · {{ item.vectorCount }} 条向量</small>
            </div>
            <div class="recent-status">
              <el-tag type="success" effect="plain">已入库</el-tag>
              <time>{{ formatUploadTime(item.uploadedAt) }}</time>
            </div>
          </article>
        </div>
        <el-empty v-else description="暂无入库文档" :image-size="64" />
      </section>
    </div>

    <section class="format-guide" aria-labelledby="format-guide-title">
      <div class="format-guide__header">
        <div>
          <h2 id="format-guide-title">文档标准格式</h2>
          <p>结构越清楚，系统越容易按完整语义拆分内容。建议一份文档只说明一个主题。</p>
        </div>
        <span class="format-guide__flow">识别标题&nbsp;&nbsp;→&nbsp;&nbsp;按章节分段&nbsp;&nbsp;→&nbsp;&nbsp;写入向量库</span>
      </div>

      <div class="format-guide__columns">
        <article class="format-column" aria-labelledby="word-format-title">
          <div class="format-column__title">
            <span class="format-mark format-mark--word" aria-hidden="true">W</span>
            <div>
              <h3 id="word-format-title">Word 标准格式</h3>
              <p>适合制度、流程、规范等日常办公文档</p>
            </div>
          </div>

          <div class="format-column__content">
            <ul class="format-rules">
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>文档标题</b>使用“标题 1”，全文只保留一个主标题</span></li>
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>章节标题</b>使用“标题 2”，不要只用加粗代替标题</span></li>
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>规则正文</b>写清适用条件、要求、判断依据和例外</span></li>
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>图片与表格</b>可作补充，关键结论必须同时写成文字</span></li>
            </ul>

            <figure class="document-example document-example--word" aria-label="Word 文档格式示例">
              <div class="word-toolbar" aria-hidden="true">
                <span class="word-app">W</span>
                <span>标题 1</span>
                <i></i><i></i><i></i>
              </div>
              <div class="word-page">
                <h4>员工月计划编写规范</h4>
                <p class="example-meta">版本：V1.0&nbsp;&nbsp;&nbsp;适用范围：全体员工</p>
                <h5>一、文档目的</h5>
                <p>统一月计划的填写口径，使目标可以检查和验收。</p>
                <h5>二、计划目标填写规则</h5>
                <p><b>适用条件：</b>所有月度重点任务。</p>
                <p><b>填写要求：</b>必须包含完成时间、数量或验收结果。</p>
                <p><b>判断依据：</b>计划字段与对应成果证明。</p>
              </div>
              <figcaption>Word 成品示例：标题层级明确，规则内容可独立理解</figcaption>
            </figure>
          </div>
        </article>

        <article class="format-column" aria-labelledby="markdown-format-title">
          <div class="format-column__title">
            <span class="format-mark format-mark--markdown" aria-hidden="true">M↓</span>
            <div>
              <h3 id="markdown-format-title">Markdown 标准格式</h3>
              <p>适合结构固定、需要持续维护的知识文档</p>
            </div>
          </div>

          <div class="format-column__content">
            <ul class="format-rules">
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>文件编码</b>使用 UTF-8，扩展名为 .md 或 .markdown</span></li>
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>文档标题</b>使用一个“#”，章节标题使用“##”</span></li>
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>规则结构</b>使用列表逐项写明条件、要求、依据和例外</span></li>
              <li><el-icon><CircleCheckFilled /></el-icon><span><b>直接上传</b>无需填写知识编号、状态等 YAML 元数据</span></li>
            </ul>

            <figure class="document-example document-example--markdown" aria-label="Markdown 文档格式示例">
              <div class="markdown-filebar">
                <span aria-hidden="true">#</span>
                <strong>月计划编写规范.md</strong>
              </div>
              <pre><code><em># 员工月计划编写规范</em>

<b>## 适用范围</b>
适用于全体员工月计划。

<b>## RULE-001 目标需量化</b>
- 适用条件：月度重点任务
- 必须：写明时间、数量或结果
- 判断依据：计划字段与成果证明
- 例外：经负责人书面确认</code></pre>
              <figcaption>Markdown 成品示例：用标题和列表表达完整规则</figcaption>
            </figure>
          </div>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheckFilled, Document, Refresh } from '@element-plus/icons-vue'
import {
  listLeaderKnowledgeUploadsApi,
  uploadLeaderKnowledgeApi,
  type UploadedKnowledgeDocument,
} from '@/api/knowledge'
import { getLeaderOrgTreeApi } from '@/api/leader'
import type { OrgNode } from '@/api/performance'
import KnowledgeBatchUpload from '@/components/knowledge/KnowledgeBatchUpload.vue'

const listLoading = ref(false)
const recentUploads = ref<UploadedKnowledgeDocument[]>([])
const orgTree = ref<OrgNode[]>([])
const uploadKnowledgeFile = uploadLeaderKnowledgeApi

async function loadUploads() {
  listLoading.value = true
  try {
    const [rows, orgs] = await Promise.all([listLeaderKnowledgeUploadsApi(), getLeaderOrgTreeApi()])
    recentUploads.value = rows
    orgTree.value = orgs
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '入库记录加载失败')
  } finally {
    listLoading.value = false
  }
}

function formatUploadTime(value: string) {
  if (!value) return '刚刚'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

onMounted(loadUploads)
</script>

<style scoped>
.knowledge-ingest-page { max-width: 1240px; }
.ingest-layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 32px; align-items: start; }
.upload-pane, .recent-pane { min-width: 0; }
.upload-pane { padding-bottom: 4px; }
.upload-pane :deep(.el-upload) { width: 100%; }
.upload-pane :deep(.el-upload-dragger) { display: grid; min-height: 190px; place-content: center; gap: 9px; width: 100%; padding: 28px; border: 1px dashed #a9c2b7; border-radius: 8px; background: #f8faf8; }
.upload-pane :deep(.el-upload-dragger:hover) { border-color: var(--blue); background: var(--soft-blue); }
.upload-pane :deep(.el-upload-dragger strong) { color: var(--ink); font-size: 15px; }
.upload-pane :deep(.el-upload-dragger span) { color: var(--muted); font-size: 12px; }
.upload-icon { margin: 0 auto 6px; color: var(--blue); font-size: 38px; }
.upload-submit { width: 100%; margin-top: 12px; }
.upload-result { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 12px; align-items: start; margin-top: 14px; padding: 14px; border: 1px solid #bcd8c9; border-radius: 8px; background: #f1f8f3; }
.upload-result > .el-icon { margin-top: 2px; color: #2f8a69; font-size: 22px; }
.upload-result > div { min-width: 0; }
.upload-result__title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.upload-result__title strong { overflow: hidden; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.upload-result p { margin: 5px 0 3px; color: #365b4c; font-size: 12px; }
.upload-result small { display: block; color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
.recent-pane { min-height: 0; padding-top: 24px; border-top: 1px solid var(--line); }
.recent-head { display: flex; align-items: center; justify-content: space-between; min-height: 56px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.recent-head h2 { margin: 0; color: var(--ink); font-size: 16px; }
.recent-head p { margin: 5px 0 0; color: var(--muted); font-size: 12px; }
.recent-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); column-gap: 24px; }
.recent-list article { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; gap: 12px; align-items: center; min-height: 82px; padding: 13px 2px; border-bottom: 1px solid var(--line); }
.recent-list article > .el-icon { color: var(--blue); font-size: 20px; }
.recent-copy { display: grid; gap: 3px; min-width: 0; }
.recent-copy strong, .recent-copy span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-copy strong { color: var(--ink); font-size: 13px; }
.recent-copy span, .recent-copy small, .recent-status time { color: var(--muted); font-size: 11px; }
.recent-status { display: grid; justify-items: end; gap: 6px; }
.recent-status time { white-space: nowrap; }
.format-guide { margin-top: 34px; padding-top: 28px; border-top: 1px solid var(--line); }
.format-guide__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 22px; }
.format-guide__header h2 { margin: 0; color: var(--ink); font-size: 18px; }
.format-guide__header p { max-width: 68ch; margin: 6px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }
.format-guide__flow { flex: 0 0 auto; padding: 7px 10px; border-radius: 7px; color: #41685e; background: #edf4f0; font-size: 11px; white-space: nowrap; }
.format-guide__columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--line); border-radius: 10px; background: #fbfcf9; }
.format-column { min-width: 0; padding: 20px; }
.format-column + .format-column { border-left: 1px solid var(--line); }
.format-column__title { display: flex; align-items: center; gap: 11px; min-height: 42px; margin-bottom: 17px; }
.format-column__title > div { min-width: 0; }
.format-column__title h3 { margin: 0; color: var(--ink); font-size: 15px; }
.format-column__title p { margin: 4px 0 0; color: var(--muted); font-size: 11px; line-height: 1.45; }
.format-mark { display: grid; width: 34px; height: 34px; flex: 0 0 auto; place-items: center; border-radius: 7px; color: #fff; font-weight: 800; }
.format-mark--word { background: #2b579a; }
.format-mark--markdown { color: #24493f; border: 1px solid #aac6bc; background: #e8f2ed; font-size: 11px; }
.format-column__content { display: grid; grid-template-columns: minmax(180px, .86fr) minmax(240px, 1.14fr); gap: 20px; align-items: stretch; }
.format-rules { display: grid; align-content: start; gap: 13px; margin: 0; padding: 0; list-style: none; }
.format-rules li { display: grid; grid-template-columns: 16px minmax(0, 1fr); gap: 8px; color: #566a64; font-size: 11px; line-height: 1.6; }
.format-rules .el-icon { margin-top: 2px; color: #34816a; font-size: 14px; }
.format-rules b { color: var(--ink); font-weight: 750; }
.document-example { min-width: 0; margin: 0; overflow: hidden; border-radius: 7px; background: #fff; box-shadow: 0 2px 7px rgb(36 61 53 / 9%); }
.document-example figcaption { min-height: 34px; padding: 9px 11px; color: #61736d; background: #f1f4f1; font-size: 9px; line-height: 1.5; }
.word-toolbar { display: flex; align-items: center; gap: 7px; height: 28px; padding: 0 9px; color: #67746f; background: #edf1ee; font-size: 8px; }
.word-toolbar .word-app { display: grid; width: 18px; height: 18px; place-items: center; color: #fff; background: #2b579a; font-size: 9px; font-weight: 800; }
.word-toolbar i { width: 19px; height: 4px; border-radius: 2px; background: #cfd7d2; }
.word-page { min-height: 212px; margin: 9px 18px 12px; padding: 17px 20px; border: 1px solid #e3e7e4; color: #2c3431; background: #fff; }
.word-page h4 { margin: 0 0 6px; font-family: "Microsoft YaHei", sans-serif; font-size: 14px; text-align: center; }
.word-page .example-meta { margin-bottom: 12px; color: #7b8581; font-size: 7px; text-align: center; }
.word-page h5 { margin: 9px 0 4px; font-size: 9px; }
.word-page p { margin: 3px 0; font-size: 7px; line-height: 1.55; }
.document-example--markdown { color: #dfe9e4; background: #163b34; }
.markdown-filebar { display: flex; align-items: center; gap: 7px; height: 30px; padding: 0 11px; color: #a8c1b8; background: #102f29; font-size: 8px; }
.markdown-filebar span { color: #f0ad5d; font-size: 13px; font-weight: 800; }
.markdown-filebar strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.document-example--markdown pre { min-height: 211px; margin: 0; padding: 16px 18px; overflow: hidden; font: 8px/1.72 "Cascadia Mono", "Microsoft YaHei", monospace; white-space: pre-wrap; }
.document-example--markdown em { color: #efbd7f; font-style: normal; }
.document-example--markdown b { color: #78c5a6; font-weight: 700; }
.document-example--markdown figcaption { color: #b9cbc5; background: #102f29; }
@media (max-width: 1440px) { .format-column__content { grid-template-columns: 1fr; } }
@media (max-width: 900px) { .format-guide__columns { grid-template-columns: 1fr; } .upload-pane :deep(.el-upload-dragger) { min-height: 220px; } .format-column + .format-column { border-top: 1px solid var(--line); border-left: 0; } .format-column__content { grid-template-columns: minmax(180px, .8fr) minmax(240px, 1.2fr); } }
@media (max-width: 680px) { .format-guide__header { display: grid; } .format-guide__flow { width: fit-content; max-width: 100%; white-space: normal; } .format-column__content { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .recent-list { grid-template-columns: 1fr; } .recent-list article { grid-template-columns: 24px minmax(0, 1fr); } .recent-status { grid-column: 2; justify-items: start; } .format-column { padding: 16px; } .word-page { margin-inline: 10px; padding-inline: 14px; } }
</style>
