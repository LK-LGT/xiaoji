<template>
  <section class="page-panel knowledge-page" v-loading="loading">
    <div class="page-header knowledge-header">
      <div>
        <span class="eyebrow">ENTERPRISE KNOWLEDGE</span>
        <h1 class="page-title">企业知识</h1>
        <p class="page-subtitle">发布依据、适用范围、版本关系与运行同步状态。</p>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button :icon="UploadFilled" @click="activeTab = 'upload'">上传文档</el-button>
        <el-button type="primary" :icon="RefreshRight" :loading="syncing" @click="syncKnowledge">同步 Vault</el-button>
      </div>
    </div>

    <div class="knowledge-runtime" :class="{ 'has-error': !status.vaultAvailable || !status.vectorAvailable || status.lastSync?.status === 'FAILED' }">
      <div class="runtime-state">
        <span class="runtime-dot" :class="runtimeClass"></span>
        <div>
          <strong>{{ runtimeTitle }}</strong>
          <small>{{ status.lastSync ? `${formatTime(status.lastSync.finishedAt || status.lastSync.startedAt)} · ${syncLabel(status.lastSync.status)}` : '尚无同步记录' }}</small>
        </div>
      </div>
      <div class="runtime-flow" aria-label="知识运行链路">
        <span>Obsidian Vault</span><b>→</b><span>MySQL</span><b>→</b><span>Qdrant</span><b>→</b><span>AI</span>
      </div>
      <code :title="status.vaultPath">{{ status.vaultPath || '未配置 Vault 路径' }}</code>
    </div>

    <div class="knowledge-metrics" aria-label="知识库统计">
      <div><span>已发布</span><strong>{{ countOf('PUBLISHED') }}</strong></div>
      <div><span>已停用</span><strong>{{ countOf('DISABLED') }}</strong></div>
      <div><span>待审核</span><strong>{{ countOf('IN_REVIEW') + countOf('APPROVED') }}</strong></div>
      <div><span>知识分块</span><strong>{{ status.chunkCount }}</strong></div>
      <div><span>Qdrant 向量点</span><strong>{{ status.vectorPointCount }}</strong></div>
      <div><span>图谱关系</span><strong>{{ status.relationCount }}</strong></div>
    </div>

    <el-alert
      v-if="!status.vectorAvailable"
      class="sync-alert"
      type="error"
      :closable="false"
      show-icon
      title="Qdrant 向量库不可用，知识上传和 AI 检索已停止"
    >
      <template #default><p>{{ status.vectorError || '请检查 Qdrant 服务和连接配置' }}</p></template>
    </el-alert>

    <el-alert
      v-if="status.lastSync?.errors.length"
      class="sync-alert"
      type="error"
      :closable="false"
      show-icon
      :title="`最近同步有 ${status.lastSync.failedCount} 个问题`"
    >
      <template #default>
        <p v-for="error in status.lastSync.errors.slice(0, 3)" :key="error">{{ error }}</p>
      </template>
    </el-alert>

    <el-tabs v-model="activeTab" class="knowledge-tabs">
      <el-tab-pane label="文档入库" name="upload">
        <div class="ingest-layout">
          <section class="upload-pane" aria-labelledby="upload-title">
            <div class="section-header">
              <div>
                <h2 id="upload-title">上传文档</h2>
                <p>支持 Word 和 Markdown，单个文件不超过 10 MB。</p>
              </div>
            </div>

            <KnowledgeBatchUpload
              :orgs="systemOptions.departments"
              :upload-request="uploadKnowledgeFile"
              @uploaded="handleBatchUploaded"
            />
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
      </el-tab-pane>

      <el-tab-pane label="文档目录" name="documents">
        <div class="filter-bar knowledge-filter">
          <el-input v-model="filters.keyword" :prefix-icon="Search" clearable placeholder="编号、标题、负责人" />
          <el-select v-model="filters.status" clearable placeholder="发布状态">
            <el-option label="已发布" value="PUBLISHED" />
            <el-option label="已停用" value="DISABLED" />
            <el-option label="待审核" value="IN_REVIEW" />
            <el-option label="已批准" value="APPROVED" />
            <el-option label="草稿" value="DRAFT" />
            <el-option label="已废止" value="DEPRECATED" />
          </el-select>
          <el-select v-model="filters.binding" clearable placeholder="约束等级">
            <el-option label="正式规范" value="NORMATIVE" />
            <el-option label="参考资料" value="REFERENCE" />
            <el-option label="案例" value="EXAMPLE" />
          </el-select>
          <span class="filter-total">{{ filteredDocuments.length }} / {{ documents.length }} 篇</span>
        </div>

        <div class="knowledge-management-note">
          <el-icon><InfoFilled /></el-icon>
          <p>所有文档都可停用、恢复或删除；<strong>页面上传文档</strong>还可直接更新版本。删除 Vault 文档时，源文件会移入可恢复归档，后续同步不会重新导入。</p>
        </div>

        <div class="table-card knowledge-table-wrap">
          <el-table :data="filteredDocuments" row-key="id" empty-text="暂无已同步文档">
            <el-table-column label="知识文档" min-width="280">
              <template #default="{ row }">
                <button class="document-link" type="button" @click="openDocument(row)">
                  <strong>{{ row.title }}</strong>
                  <span>{{ row.knowledgeId }} · v{{ row.version }} · {{ sourceLabel(row.sourceType) }}</span>
                </button>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }"><el-tag :type="documentStatusTag(row)" effect="plain">{{ documentStatusLabel(row) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="约束" width="110" align="center">
              <template #default="{ row }"><el-tag :type="bindingTag(row.binding)" effect="plain">{{ bindingLabel(row.binding) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="分类范围" min-width="250">
              <template #default="{ row }"><span class="scope-text">{{ knowledgeTypeLabel(row.knowledgeType) }} · {{ audienceSummary(row) }} · {{ documentScopeLabel(row) }}</span></template>
            </el-table-column>
            <el-table-column prop="owner" label="编写人" width="120" />
            <el-table-column prop="reviewer" label="审核人" width="120"><template #default="{ row }">{{ row.reviewer || '—' }}</template></el-table-column>
            <el-table-column label="向量索引" width="120" align="center">
              <template #default="{ row }"><el-tag :type="indexTag(row.indexStatus)" effect="plain">{{ indexLabel(row.indexStatus) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="内容" width="130" align="center"><template #default="{ row }">{{ row.chunkCount }} 段 / {{ row.vectorCount }} 向量</template></el-table-column>
            <el-table-column label="操作" width="136" fixed="right">
              <template #default="{ row }">
                <div class="document-actions">
                  <el-button link type="primary" :icon="View" @click="openDocument(row)">查看</el-button>
                  <el-dropdown trigger="click" :disabled="actionDocumentId === row.id" @command="handleDocumentCommand($event, row)">
                    <el-button
                      link
                      :icon="MoreFilled"
                      :loading="actionDocumentId === row.id"
                      title="更多操作"
                      :aria-label="`${row.title}的更多操作`"
                    />
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item command="history" :icon="Clock">版本历史</el-dropdown-item>
                        <el-dropdown-item v-if="row.sourceType === 'UPLOAD'" command="update" :icon="UploadFilled">更新版本</el-dropdown-item>
                        <el-dropdown-item
                          v-if="row.manuallyDisabled"
                          command="enable"
                          :icon="VideoPlay"
                          :disabled="row.status !== 'PUBLISHED'"
                        >恢复启用</el-dropdown-item>
                        <el-dropdown-item v-else-if="row.status === 'PUBLISHED'" command="disable" :icon="VideoPause">停用知识</el-dropdown-item>
                        <el-dropdown-item command="delete" :icon="Delete" divided class="danger-menu-item">删除知识</el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="检索验证" name="retrieval">
        <div class="retrieval-layout">
          <div class="retrieval-form">
            <div class="retrieval-form__head">
              <div><h2>运行时检索</h2><p>查看同一段业务内容进入 AI 前匹配到的实际依据。</p></div>
              <el-tag effect="plain">仅检索已发布内容</el-tag>
            </div>
            <el-form label-position="top">
              <div class="retrieval-fields">
                <el-form-item label="业务对象">
                  <el-select v-model="previewForm.appliesTo">
                    <el-option v-for="item in scopes" :key="item.value" :label="item.label" :value="item.value" />
                  </el-select>
                </el-form-item>
                <el-form-item label="部门 ID（可选）">
                  <el-input-number v-model="previewForm.deptId" :min="1" :controls="false" placeholder="全局范围" />
                </el-form-item>
              </div>
              <el-form-item label="业务内容">
                <el-input v-model="previewForm.query" type="textarea" :rows="6" maxlength="3000" show-word-limit placeholder="输入计划内容、交付物或成果说明" />
              </el-form-item>
              <el-button type="primary" :icon="Search" :loading="previewing" @click="runPreview">执行检索</el-button>
            </el-form>
          </div>

          <div class="retrieval-results" v-loading="previewing">
            <div class="retrieval-results__head">
              <div><h2>命中依据</h2><p>{{ previewRan ? `返回 ${previewResults.length} 个知识片段` : '等待检索' }}</p></div>
            </div>
            <div v-if="previewResults.length" class="knowledge-hit-list">
              <article v-for="hit in previewResults" :key="`${hit.knowledgeId}-${hit.headingPath}-${hit.checksum}`" class="knowledge-hit">
                <div class="knowledge-hit__head">
                  <el-tag :type="bindingTag(hit.binding)" effect="plain">{{ bindingLabel(hit.binding) }}</el-tag>
                  <strong>{{ hit.citation }}</strong>
                  <span>{{ hit.score.toFixed(2) }}</span>
                </div>
                <p>{{ hit.content }}</p>
              </article>
            </div>
            <el-empty v-else :description="previewRan ? '没有符合范围的已发布知识' : '输入内容后执行检索'" :image-size="72" />
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="drawerOpen" size="min(680px, 94vw)" title="知识文档详情" destroy-on-close>
      <div v-if="detail" class="knowledge-detail">
        <div class="detail-title">
          <span>{{ detail.document.knowledgeId }} · v{{ detail.document.version }}</span>
          <h2>{{ detail.document.title }}</h2>
          <div><el-tag :type="statusTag(detail.document.status)" effect="plain">{{ statusLabel(detail.document.status) }}</el-tag><el-tag :type="bindingTag(detail.document.binding)" effect="plain">{{ bindingLabel(detail.document.binding) }}</el-tag></div>
        </div>
        <dl class="detail-meta">
          <div><dt>编写 / 审核</dt><dd>{{ detail.document.owner }} / {{ detail.document.reviewer || '未指定' }}</dd></div>
          <div><dt>文档类型 / 约束</dt><dd>{{ knowledgeTypeLabel(detail.document.knowledgeType) }} / {{ bindingLabel(detail.document.binding) }}</dd></div>
          <div><dt>适用角色 / 范围</dt><dd>{{ audienceSummary(detail.document) }} / {{ documentScopeLabel(detail.document) }}</dd></div>
          <div><dt>适用业务</dt><dd>{{ detail.document.appliesTo.map(scopeLabel).join('、') || '未限定' }}</dd></div>
          <div><dt>源文件</dt><dd>{{ detail.document.sourcePath }}</dd></div>
        </dl>
        <section class="detail-section">
          <h3><el-icon><Link /></el-icon> 图谱关系</h3>
          <div v-if="detail.relations.length" class="relation-list"><span v-for="relation in detail.relations" :key="`${relation.type}-${relation.target}`"><b>{{ relation.type }}</b>{{ relation.target }}</span></div>
          <p v-else class="muted-line">暂无显式关系</p>
        </section>
        <section class="detail-section">
          <h3><el-icon><Document /></el-icon> 知识分块</h3>
          <article v-for="chunk in detail.chunks" :key="chunk.chunkNo" class="chunk-row">
            <div><span>#{{ chunk.chunkNo }}</span><strong>{{ chunk.headingPath || '正文' }}</strong></div>
            <p>{{ chunk.content }}</p>
          </article>
        </section>
      </div>
    </el-drawer>

    <el-drawer v-model="historyOpen" size="min(560px, 94vw)" title="版本与操作记录" destroy-on-close>
      <div v-loading="historyLoading" class="knowledge-history">
        <div v-if="historyTarget" class="history-heading">
          <strong>{{ historyTarget.title }}</strong>
          <span>{{ historyTarget.knowledgeId }} · 当前 v{{ historyTarget.version }}</span>
        </div>
        <el-timeline v-if="historyEntries.length" class="history-timeline">
          <el-timeline-item
            v-for="entry in historyEntries"
            :key="entry.id ?? `current-${entry.documentId}`"
            :timestamp="formatTime(entry.createdAt)"
            placement="top"
            :type="entry.operation === 'CURRENT' ? 'primary' : entry.operation === 'DELETED' ? 'danger' : ''"
          >
            <article class="history-entry">
              <div class="history-entry__head">
                <strong>{{ historyOperationLabel(entry.operation) }}</strong>
                <el-tag size="small" :type="statusTag(entry.status)" effect="plain">{{ statusLabel(entry.status) }}</el-tag>
              </div>
              <p>v{{ entry.version }} · {{ entry.chunkCount }} 个片段 · {{ entry.vectorCount }} 条向量</p>
              <p v-if="entry.changeNote" class="history-entry__note">{{ entry.changeNote }}</p>
              <small>{{ entry.operatorName }} · {{ entry.originalFileName || sourceLabel(entry.sourceType) }}</small>
            </article>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无版本记录" :image-size="72" />
      </div>
    </el-drawer>

    <el-dialog
      v-model="versionDialogOpen"
      width="min(520px, 94vw)"
      title="更新知识版本"
      :close-on-click-modal="false"
      :close-on-press-escape="!versionUpdating"
      :show-close="!versionUpdating"
      @closed="resetVersionForm"
    >
      <div v-if="versionTarget" class="version-dialog">
        <div class="version-current">
          <div><span>当前文档</span><strong>{{ versionTarget.title }}</strong></div>
          <el-tag effect="plain">v{{ versionTarget.version }}</el-tag>
        </div>
        <el-form label-position="top">
          <el-form-item label="新版本文档" required>
            <el-upload
              class="version-file-upload"
              action="#"
              accept=".doc,.docx,.md,.markdown"
              :auto-upload="false"
              :limit="1"
              :file-list="versionFiles"
              :disabled="versionUpdating"
              :on-change="selectVersionFile"
              :on-remove="removeVersionFile"
            >
              <el-button :icon="UploadFilled">选择 Word 或 Markdown</el-button>
              <template #tip><div class="el-upload__tip">新文件将替换当前检索内容，旧版本保留在历史记录中。</div></template>
            </el-upload>
          </el-form-item>
          <el-form-item label="新版本号" required>
            <el-input v-model="versionForm.version" maxlength="30" placeholder="例如 1.1" />
          </el-form-item>
          <el-form-item label="更新说明">
            <el-input v-model="versionForm.changeNote" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="说明本次修订的内容" />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button :disabled="versionUpdating" @click="versionDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="versionUpdating" @click="submitVersionUpdate">确认更新</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadFile, type UploadFiles, type UploadRawFile } from 'element-plus'
import {
  CircleCheckFilled, Clock, Delete, Document, InfoFilled, Link, MoreFilled, Refresh, RefreshRight,
  Search, UploadFilled, VideoPause, VideoPlay, View,
} from '@element-plus/icons-vue'
import {
  deleteKnowledgeDocumentApi,
  disableKnowledgeDocumentApi,
  enableKnowledgeDocumentApi,
  getKnowledgeDocumentApi,
  getKnowledgeDocumentHistoryApi,
  getKnowledgeStatusApi,
  listLeaderKnowledgeUploadsApi,
  listKnowledgeDocumentsApi,
  previewKnowledgeApi,
  syncKnowledgeApi,
  updateKnowledgeDocumentVersionApi,
  uploadLeaderKnowledgeApi,
  type KnowledgeActionResult,
  type KnowledgeDocument,
  type KnowledgeDocumentDetail,
  type KnowledgeDocumentHistory,
  type KnowledgeHit,
  type KnowledgeStatus,
  type UploadedKnowledgeDocument,
} from '@/api/knowledge'
import KnowledgeBatchUpload from '@/components/knowledge/KnowledgeBatchUpload.vue'
import { getSystemOptions, type SystemOptions } from '@/api/system'

const emptyStatus = (): KnowledgeStatus => ({ enabled: false, vaultPath: '', vaultAvailable: false, documentCounts: {}, chunkCount: 0, relationCount: 0, vectorEngine: 'Qdrant', vectorAvailable: false, vectorCollections: [], vectorPointCount: 0, vectorError: null, lastSync: null })
const loading = ref(false)
const syncing = ref(false)
const listLoading = ref(false)
const previewing = ref(false)
const previewRan = ref(false)
const activeTab = ref('upload')
const drawerOpen = ref(false)
const historyOpen = ref(false)
const historyLoading = ref(false)
const versionDialogOpen = ref(false)
const versionUpdating = ref(false)
const actionDocumentId = ref<number | null>(null)
const status = reactive<KnowledgeStatus>(emptyStatus())
const documents = ref<KnowledgeDocument[]>([])
const detail = ref<KnowledgeDocumentDetail | null>(null)
const previewResults = ref<KnowledgeHit[]>([])
const systemOptions = reactive<SystemOptions>({ departments: [], leaders: [], roles: [] })
const recentUploads = ref<UploadedKnowledgeDocument[]>([])
const historyEntries = ref<KnowledgeDocumentHistory[]>([])
const historyTarget = ref<KnowledgeDocument | null>(null)
const versionTarget = ref<KnowledgeDocument | null>(null)
const versionFile = ref<File | null>(null)
const versionFiles = ref<UploadFile[]>([])
const versionForm = reactive({ version: '', changeNote: '' })
const filters = reactive({ keyword: '', status: '', binding: '' })
const previewForm = reactive<{ appliesTo: string; query: string; deptId?: string }>({ appliesTo: 'MONTH_PLAN', query: '' })
const allowedExtensions = ['.doc', '.docx', '.md', '.markdown']
const maxFileSize = 10 * 1024 * 1024
const scopes = [
  { label: '月计划', value: 'MONTH_PLAN' }, { label: '周计划', value: 'WEEK_PLAN' },
  { label: '日计划', value: 'DAY_PLAN' },
  { label: '计划检查', value: 'PLAN_CHECK' }, { label: '计划审批', value: 'APPROVAL' },
  { label: '成果提交', value: 'RESULT' }, { label: '成果确认', value: 'RESULT_CONFIRM' },
]
const uploadKnowledgeFile = uploadLeaderKnowledgeApi

const filteredDocuments = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return documents.value.filter((item) => {
    const text = `${item.knowledgeId} ${item.title} ${item.owner} ${item.reviewer || ''}`.toLowerCase()
    const statusMatches = !filters.status
      || (filters.status === 'DISABLED' ? item.manuallyDisabled : !item.manuallyDisabled && item.status === filters.status)
    return (!keyword || text.includes(keyword))
      && statusMatches
      && (!filters.binding || item.binding === filters.binding)
  })
})
const runtimeClass = computed(() => status.vaultAvailable && status.vectorAvailable && status.lastSync?.status !== 'FAILED' ? 'is-ok' : 'is-error')
const runtimeTitle = computed(() => {
  if (!status.enabled) return '知识同步已关闭'
  if (!status.vaultAvailable) return 'Vault 不可用'
  if (!status.vectorAvailable) return 'Qdrant 不可用'
  if (status.lastSync?.status === 'FAILED') return '最近同步失败'
  if (status.lastSync?.status === 'PARTIAL') return '部分文档同步失败'
  return '知识运行正常'
})

async function load() {
  loading.value = true
  try {
    const [runtime, rows, options] = await Promise.all([
      getKnowledgeStatusApi(), listKnowledgeDocumentsApi(), getSystemOptions(),
    ])
    Object.assign(status, runtime)
    documents.value = rows
    Object.assign(systemOptions, options)
  } catch (error) { showError(error, '企业知识加载失败') }
  finally { loading.value = false }
}
async function syncKnowledge() {
  syncing.value = true
  try {
    const result = await syncKnowledgeApi()
    ElMessage[result.status === 'SUCCESS' ? 'success' : 'warning'](
      result.status === 'SUCCESS' ? `同步完成，共发布 ${result.publishedCount} 篇文档` : `同步完成，但有 ${result.failedCount} 个问题`,
    )
    await load()
  } catch (error) { showError(error, '知识同步失败') }
  finally { syncing.value = false }
}
function validateFile(file: UploadRawFile) {
  const fileName = file.name.toLowerCase()
  if (!allowedExtensions.some((extension) => fileName.endsWith(extension))) {
    ElMessage.error('仅支持 Word 或 Markdown 文档')
    return false
  }
  if (file.size > maxFileSize) {
    ElMessage.error('单个文件不能超过 10 MB')
    return false
  }
  return true
}
async function loadUploads() {
  listLoading.value = true
  try { recentUploads.value = await listLeaderKnowledgeUploadsApi() }
  catch (error) { showError(error, '入库记录加载失败') }
  finally { listLoading.value = false }
}
async function handleBatchUploaded() {
  await Promise.all([load(), loadUploads()])
  activeTab.value = 'upload'
}
async function openDocument(row: KnowledgeDocument) {
  historyOpen.value = false
  drawerOpen.value = true
  detail.value = null
  try { detail.value = await getKnowledgeDocumentApi(row.id) }
  catch (error) { drawerOpen.value = false; showError(error, '文档详情加载失败') }
}
async function openHistory(row: KnowledgeDocument) {
  drawerOpen.value = false
  historyTarget.value = row
  historyEntries.value = []
  historyOpen.value = true
  historyLoading.value = true
  try { historyEntries.value = await getKnowledgeDocumentHistoryApi(row.id) }
  catch (error) { historyOpen.value = false; showError(error, '版本历史加载失败') }
  finally { historyLoading.value = false }
}
function openVersionDialog(row: KnowledgeDocument) {
  versionTarget.value = row
  versionForm.version = nextVersion(row.version)
  versionForm.changeNote = ''
  versionFile.value = null
  versionFiles.value = []
  versionDialogOpen.value = true
}
function selectVersionFile(file: UploadFile, files: UploadFiles) {
  if (!file.raw || !validateFile(file.raw)) {
    versionFile.value = null
    versionFiles.value = []
    return
  }
  versionFile.value = file.raw
  versionFiles.value = files.slice(-1)
}
function removeVersionFile() {
  versionFile.value = null
  versionFiles.value = []
}
function resetVersionForm() {
  if (versionUpdating.value) return
  versionTarget.value = null
  versionFile.value = null
  versionFiles.value = []
  versionForm.version = ''
  versionForm.changeNote = ''
}
async function submitVersionUpdate() {
  if (!versionTarget.value) return
  if (!versionFile.value) return ElMessage.warning('请选择新版本文档')
  if (!versionForm.version.trim()) return ElMessage.warning('请填写新版本号')
  versionUpdating.value = true
  try {
    const result = await updateKnowledgeDocumentVersionApi(
      versionTarget.value.id, versionFile.value, versionForm.version.trim(), versionForm.changeNote.trim(),
    )
    ElMessage.success(`已更新为 v${versionForm.version.trim()}，生成 ${result.vectorCount} 条向量`)
    versionDialogOpen.value = false
    await Promise.all([load(), loadUploads()])
    activeTab.value = 'documents'
  } catch (error) { showError(error, '知识版本更新失败') }
  finally { versionUpdating.value = false }
}
async function handleDocumentCommand(command: string, row: KnowledgeDocument) {
  if (command === 'history') return openHistory(row)
  if (command === 'update') return openVersionDialog(row)
  if (command === 'enable') return enableDocument(row)
  if (command === 'disable') return disableDocument(row)
  if (command === 'delete') return deleteDocument(row)
}
async function disableDocument(row: KnowledgeDocument) {
  try {
    await ElMessageBox.confirm(
      `停用后“${row.title}”将立即退出 AI 检索，但文档和历史记录会保留，可随时恢复。`,
      '确认停用知识',
      { confirmButtonText: '确认停用', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  await runDocumentAction(row, () => disableKnowledgeDocumentApi(row.id), '知识停用失败')
}
async function enableDocument(row: KnowledgeDocument) {
  await runDocumentAction(row, () => enableKnowledgeDocumentApi(row.id), '知识恢复失败')
}
async function deleteDocument(row: KnowledgeDocument) {
  const sourceNotice = row.sourceType === 'VAULT'
    ? '对应 Vault 源文件会移入 .deleted 可恢复归档，后续同步不会重新导入。'
    : '原始上传文件、版本历史和审计记录会保留在系统归档中。'
  try {
    await ElMessageBox.confirm(
      `将删除“${row.title}”，并移出 AI 检索。${sourceNotice}此操作不能在页面撤销。`,
      '确认删除知识',
      { confirmButtonText: '删除知识', cancelButtonText: '取消', type: 'error' },
    )
  } catch { return }
  await runDocumentAction(row, () => deleteKnowledgeDocumentApi(row.id), '知识删除失败')
}
async function runDocumentAction(row: KnowledgeDocument, request: () => Promise<KnowledgeActionResult>, fallback: string) {
  actionDocumentId.value = row.id
  try {
    const result = await request()
    ElMessage[result.vectorCleanupSucceeded ? 'success' : 'warning'](result.message)
    await Promise.all([load(), loadUploads()])
  } catch (error) { showError(error, fallback) }
  finally { actionDocumentId.value = null }
}
async function runPreview() {
  if (!previewForm.query.trim()) return ElMessage.warning('请输入需要检索的业务内容')
  previewing.value = true
  try {
    previewResults.value = await previewKnowledgeApi({ appliesTo: previewForm.appliesTo, query: previewForm.query.trim(), deptId: previewForm.deptId })
    previewRan.value = true
  } catch (error) { showError(error, '检索验证失败') }
  finally { previewing.value = false }
}
function countOf(key: string) { return Number(status.documentCounts[key] || 0) }
function syncLabel(value: string) { return ({ SUCCESS: '同步成功', PARTIAL: '部分成功', FAILED: '同步失败', RUNNING: '同步中' } as Record<string, string>)[value] || value }
function statusLabel(value: string) { return ({ PUBLISHED: '已发布', DISABLED: '已停用', DELETED: '已删除', IN_REVIEW: '待审核', APPROVED: '已批准', DRAFT: '草稿', DEPRECATED: '已废止' } as Record<string, string>)[value] || value }
function statusTag(value: string) { return ({ PUBLISHED: 'success', DISABLED: 'warning', DELETED: 'danger', IN_REVIEW: 'warning', APPROVED: 'primary', DRAFT: 'info', DEPRECATED: 'info' } as Record<string, 'success' | 'warning' | 'danger' | 'primary' | 'info'>)[value] || 'info' }
function documentStatusLabel(row: KnowledgeDocument) { return row.manuallyDisabled ? statusLabel('DISABLED') : statusLabel(row.status) }
function documentStatusTag(row: KnowledgeDocument) { return row.manuallyDisabled ? statusTag('DISABLED') : statusTag(row.status) }
function bindingLabel(value: string) { return ({ NORMATIVE: '正式规范', REFERENCE: '参考资料', EXAMPLE: '案例' } as Record<string, string>)[value] || value }
function bindingTag(value: string) { return value === 'NORMATIVE' ? 'danger' : value === 'REFERENCE' ? 'primary' : 'info' }
function indexLabel(value: string) { return ({ READY: '已入 Qdrant', PENDING: '待索引', FAILED: '索引失败', NOT_REQUIRED: '无需索引' } as Record<string, string>)[value] || value }
function indexTag(value: string) { return ({ READY: 'success', PENDING: 'warning', FAILED: 'danger', NOT_REQUIRED: 'info' } as Record<string, 'success' | 'warning' | 'danger' | 'info'>)[value] || 'info' }
function scopeLabel(value: string) { return scopes.find((item) => item.value === value)?.label || value }
function knowledgeTypeLabel(value: string) { return ({ HISTORICAL_PLAN: '历史计划', JOB_DESCRIPTION: '岗位说明', DEPARTMENT_GOAL: '部门目标', LEADER_REQUIREMENT: '领导要求', PROJECT_MATERIAL: '项目资料', POLICY: '制度规范', ACCEPTANCE_STANDARD: '验收标准', REFERENCE_CASE: '参考案例', UPLOADED_REFERENCE: '上传资料' } as Record<string, string>)[value] || value }
function audienceSummary(row: KnowledgeDocument) { return row.audienceCodes?.length ? row.audienceCodes.map((value) => value === 'MANAGEMENT' ? '管理端' : '员工端').join('、') : '全部角色' }
function documentScopeLabel(row: KnowledgeDocument) { return row.scopeType === 'GLOBAL' || !row.scopeType ? '全公司' : row.scopeOrgName || '指定组织' }
function sourceLabel(value: string) { return value === 'UPLOAD' ? '页面上传' : 'Vault 管理' }
function historyOperationLabel(value: string) { return ({ CURRENT: '当前版本', VERSION_REPLACED: '历史版本', DISABLED: '已停用', ENABLED: '恢复启用', DELETED: '已删除' } as Record<string, string>)[value] || value }
function nextVersion(value: string) {
  const parts = value.split('.').map(Number)
  if (!parts.length || parts.some(Number.isNaN)) return ''
  if (parts.length === 1) return `${parts[0] + 1}.0`
  parts[parts.length - 1] += 1
  return parts.join('.')
}
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function formatUploadTime(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '刚刚' }
function showError(error: unknown, fallback: string) { ElMessage.error(error instanceof Error ? error.message : fallback) }
onMounted(() => {
  load()
  loadUploads()
})
</script>

<style scoped>
.knowledge-page { min-width: 0; }
.knowledge-header { align-items: center; }
.knowledge-runtime { display: grid; grid-template-columns: minmax(180px, .8fr) minmax(330px, 1.2fr) minmax(180px, 1fr); gap: 18px; align-items: center; min-height: 68px; margin-bottom: 0; padding: 12px 16px; border-block: 1px solid var(--line); background: #f5f8f5; }
.knowledge-runtime.has-error { background: #fff7f3; }
.runtime-state { display: flex; align-items: center; gap: 11px; min-width: 0; }
.runtime-state > div { display: grid; gap: 3px; min-width: 0; }
.runtime-state strong { color: var(--ink); font-size: 13px; }
.runtime-state small { color: var(--muted); font-size: 10px; }
.runtime-dot { width: 9px; height: 9px; flex: 0 0 auto; border-radius: 50%; background: #a9b5af; }
.runtime-dot.is-ok { background: #2f8a69; box-shadow: 0 0 0 4px rgb(47 138 105 / 12%); }
.runtime-dot.is-error { background: #c34f36; box-shadow: 0 0 0 4px rgb(195 79 54 / 12%); }
.runtime-flow { display: flex; align-items: center; justify-content: center; gap: 9px; min-width: 0; color: #48665f; font-size: 11px; white-space: nowrap; }
.runtime-flow span { padding: 4px 6px; border-bottom: 1px solid #a8beb6; }
.runtime-flow b { color: #8aa098; font-weight: 500; }
.knowledge-runtime code { overflow: hidden; color: #60736d; font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 10px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.knowledge-metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); border-bottom: 1px solid var(--line); }
.knowledge-metrics > div { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; padding: 15px 18px; border-right: 1px solid var(--line); }
.knowledge-metrics > div:last-child { border-right: 0; }
.knowledge-metrics span { color: var(--muted); font-size: 11px; }
.knowledge-metrics strong { color: var(--ink); font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 21px; }
.sync-alert { margin-top: 16px; }
.sync-alert p { margin: 3px 0; font-size: 11px; }
.knowledge-tabs { margin-top: 18px; }
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
.recent-head h2, .section-header h2 { margin: 0; color: var(--ink); font-size: 16px; }
.recent-head p, .section-header p { margin: 5px 0 0; color: var(--muted); font-size: 12px; }
.recent-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); column-gap: 24px; }
.recent-list article { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; gap: 12px; align-items: center; min-height: 82px; padding: 13px 2px; border-bottom: 1px solid var(--line); }
.recent-list article > .el-icon { color: var(--blue); font-size: 20px; }
.recent-copy { display: grid; gap: 3px; min-width: 0; }
.recent-copy strong, .recent-copy span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-copy strong { color: var(--ink); font-size: 13px; }
.recent-copy span, .recent-copy small, .recent-status time { color: var(--muted); font-size: 11px; }
.recent-status { display: grid; justify-items: end; gap: 6px; }
.recent-status time { white-space: nowrap; }
.knowledge-filter { margin-bottom: 14px; }
.filter-total { margin-left: auto; color: var(--muted); font-size: 11px; }
.knowledge-management-note { display: flex; align-items: flex-start; gap: 9px; margin: -2px 0 14px; padding: 10px 12px; color: #4d655e; background: #eef4f0; font-size: 11px; line-height: 1.6; }
.knowledge-management-note .el-icon { flex: 0 0 auto; margin-top: 2px; color: var(--blue); font-size: 15px; }
.knowledge-management-note p { margin: 0; }
.knowledge-management-note strong { color: var(--ink); }
.knowledge-table-wrap { overflow-x: auto; border-radius: 8px; }
.document-link { display: grid; gap: 4px; width: 100%; padding: 0; border: 0; color: inherit; background: transparent; text-align: left; cursor: pointer; }
.document-link strong { overflow: hidden; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.document-link span, .scope-text { color: var(--muted); font-size: 10px; }
.document-link:hover strong { color: var(--blue); }
.document-actions { display: flex; align-items: center; justify-content: center; gap: 4px; }
.document-actions .el-button + .el-button { margin-left: 0; }
:global(.danger-menu-item) { color: #b64d37; }
.retrieval-layout { display: grid; grid-template-columns: minmax(300px, 380px) minmax(0, 1fr); gap: 20px; align-items: start; }
.retrieval-form { padding-right: 20px; border-right: 1px solid var(--line); }
.retrieval-form__head, .retrieval-results__head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.retrieval-form__head h2, .retrieval-results__head h2 { margin: 0; color: var(--ink); font-size: 15px; }
.retrieval-form__head p, .retrieval-results__head p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.5; }
.retrieval-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.retrieval-fields :deep(.el-input-number), .retrieval-fields :deep(.el-select) { width: 100%; }
.retrieval-results { min-width: 0; min-height: 390px; }
.knowledge-hit-list { border-top: 1px solid var(--line); }
.knowledge-hit { padding: 15px 2px; border-bottom: 1px solid var(--line); }
.knowledge-hit__head { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 9px; align-items: center; }
.knowledge-hit__head strong { overflow: hidden; color: var(--ink); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.knowledge-hit__head span { color: var(--blue); font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 10px; }
.knowledge-hit p { display: -webkit-box; margin: 9px 0 0; overflow: hidden; color: #566b65; font-size: 11px; line-height: 1.7; -webkit-box-orient: vertical; -webkit-line-clamp: 4; }
.knowledge-detail { display: grid; gap: 22px; }
.detail-title { display: grid; gap: 7px; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
.detail-title > span { color: var(--blue); font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 10px; }
.detail-title h2 { margin: 0; color: var(--ink); font-size: 21px; line-height: 1.35; }
.detail-title > div { display: flex; gap: 7px; }
.detail-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 0; margin: 0; border-block: 1px solid var(--line); }
.detail-meta div { min-width: 0; padding: 12px 10px; border-bottom: 1px solid var(--line); }
.detail-meta div:nth-last-child(-n+2) { border-bottom: 0; }
.detail-meta div:last-child { grid-column: 1 / -1; }
.detail-meta dt { color: var(--muted); font-size: 10px; }
.detail-meta dd { overflow-wrap: anywhere; margin: 5px 0 0; color: var(--ink); font-size: 11px; }
.detail-section h3 { display: flex; align-items: center; gap: 7px; margin: 0 0 12px; color: var(--ink); font-size: 13px; }
.relation-list { display: flex; flex-wrap: wrap; gap: 7px; }
.relation-list span { display: inline-flex; gap: 6px; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; color: #5a6f69; background: #f7f9f5; font-size: 10px; }
.relation-list b { color: var(--blue); }
.muted-line { color: var(--muted); font-size: 11px; }
.chunk-row { padding: 14px 0; border-top: 1px solid var(--line); }
.chunk-row > div { display: flex; align-items: center; gap: 8px; }
.chunk-row span { color: var(--blue); font-family: "IBM Plex Mono", "Cascadia Mono", monospace; font-size: 10px; }
.chunk-row strong { color: var(--ink); font-size: 12px; }
.chunk-row p { margin: 9px 0 0; color: #586c66; font-size: 11px; line-height: 1.75; white-space: pre-wrap; }
.knowledge-history { min-height: 240px; }
.history-heading { display: grid; gap: 5px; margin-bottom: 22px; padding-bottom: 16px; border-bottom: 1px solid var(--line); }
.history-heading strong { color: var(--ink); font-size: 16px; }
.history-heading span { color: var(--muted); font-size: 10px; }
.history-timeline { padding-left: 4px; }
.history-entry { padding: 1px 0 10px; }
.history-entry__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.history-entry__head strong { color: var(--ink); font-size: 13px; }
.history-entry p { margin: 7px 0 0; color: #5b6f69; font-size: 11px; line-height: 1.55; }
.history-entry .history-entry__note { padding: 8px 10px; color: #405d55; background: #f2f6f3; }
.history-entry small { display: block; margin-top: 7px; color: var(--muted); font-size: 10px; }
.version-dialog { display: grid; gap: 18px; }
.version-current { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 14px; border-block: 1px solid var(--line); background: #f6f8f5; }
.version-current > div { display: grid; gap: 4px; min-width: 0; }
.version-current span { color: var(--muted); font-size: 10px; }
.version-current strong { overflow: hidden; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.version-file-upload { width: 100%; }
.version-file-upload :deep(.el-upload__tip) { color: var(--muted); font-size: 10px; line-height: 1.55; }
@media (max-width: 1050px) { .knowledge-runtime { grid-template-columns: 1fr 1fr; } .knowledge-runtime code { grid-column: 1 / -1; text-align: left; } .retrieval-layout { grid-template-columns: 1fr; } .retrieval-form { padding-right: 0; padding-bottom: 20px; border-right: 0; border-bottom: 1px solid var(--line); } }
@media (max-width: 700px) { .knowledge-header { align-items: flex-start; } .knowledge-runtime { grid-template-columns: 1fr; } .runtime-flow { justify-content: flex-start; overflow-x: auto; } .knowledge-runtime code { grid-column: auto; } .knowledge-metrics { grid-template-columns: 1fr 1fr; } .knowledge-metrics > div:nth-child(even) { border-right: 0; } .knowledge-metrics > div { border-bottom: 1px solid var(--line); } .knowledge-metrics > div:nth-last-child(-n+2) { border-bottom: 0; } .retrieval-fields, .detail-meta { grid-template-columns: 1fr; } .detail-meta div:nth-last-child(2) { border-bottom: 1px solid var(--line); } .upload-pane :deep(.el-upload-dragger) { min-height: 220px; } .recent-list { grid-template-columns: 1fr; } .recent-list article { grid-template-columns: 24px minmax(0, 1fr); } .recent-status { grid-column: 2; justify-items: start; } }
</style>
