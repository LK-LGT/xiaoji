<template>
  <aside class="version-navigation" aria-label="历史版本导航">
    <div class="version-navigation__groups">
      <el-segmented
        :model-value="activeGroup"
        :options="groupOptions"
        @update:model-value="updateActiveGroup"
      />
    </div>

    <div class="desktop-version-body" v-loading="loading">
      <el-alert v-if="error" class="version-navigation__error" type="error" :closable="false" show-icon :title="error">
        <template #default><el-button link type="primary" @click="emit('retry')">重新加载</el-button></template>
      </el-alert>
      <el-empty v-else-if="!loading && !versions.length" :description="emptyText" :image-size="52" />
      <nav v-else class="version-list" aria-label="版本列表">
        <button
          v-for="(version, index) in versions"
          :key="version.id"
          type="button"
          class="version-list-item"
          :class="{ active: selectedVersionId === version.id }"
          :aria-current="selectedVersionId === version.id ? 'true' : undefined"
          @click="selectVersion(version)"
        >
          <span class="version-list-item__topline">
            <strong>V{{ version.versionNo }}</strong>
            <small v-if="index === 0">最新</small>
          </span>
          <span class="version-list-item__meta">
            <b>{{ sourceLabels[version.sourceType] }}</b>
            <time>{{ formatShortTime(version.createdAt) }}</time>
          </span>
        </button>
      </nav>
    </div>

    <div class="mobile-version-body">
      <el-alert v-if="error" class="version-navigation__error" type="error" :closable="false" show-icon :title="error">
        <template #default><el-button link type="primary" @click="emit('retry')">重新加载</el-button></template>
      </el-alert>
      <el-select
        v-else
        class="mobile-version-select"
        :model-value="selectedVersionId"
        :loading="loading"
        :disabled="!versions.length"
        :placeholder="versions.length ? '选择历史版本' : emptyText"
        @update:model-value="selectVersionById"
      >
        <el-option
          v-for="version in versions"
          :key="version.id"
          :value="version.id"
          :label="mobileVersionLabel(version)"
        >
          <span class="mobile-version-option"><strong>V{{ version.versionNo }}</strong><span>{{ sourceLabels[version.sourceType] }} · {{ formatShortTime(version.createdAt) }}</span></span>
        </el-option>
      </el-select>
    </div>
  </aside>
</template>

<script setup lang="ts">
import type { PlanDraftVersionSource, PlanDraftVersionSummary } from '@/api/planDraftVersions'

const props = defineProps<{
  activeGroup: 'saved' | 'ai'
  groupOptions: Array<{ label: string; value: 'saved' | 'ai' }>
  versions: PlanDraftVersionSummary[]
  selectedVersionId?: number
  loading: boolean
  error: string
  emptyText: string
  sourceLabels: Record<PlanDraftVersionSource, string>
}>()

const emit = defineEmits<{
  'update:activeGroup': [value: 'saved' | 'ai']
  select: [version: PlanDraftVersionSummary]
  retry: []
}>()

function updateActiveGroup(value: string | number | boolean | undefined) {
  if (value === 'saved' || value === 'ai') emit('update:activeGroup', value)
}

function selectVersion(version: PlanDraftVersionSummary) {
  emit('select', version)
}

function selectVersionById(id: number) {
  const version = props.versions.find((item) => item.id === id)
  if (version) selectVersion(version)
}

function formatShortTime(value: string) {
  return value ? value.slice(5, 16).replace('T', ' ') : '-'
}

function mobileVersionLabel(version: PlanDraftVersionSummary) {
  return `V${version.versionNo} · ${props.sourceLabels[version.sourceType]} · ${formatShortTime(version.createdAt)}`
}
</script>

<style scoped>
.version-navigation { display:grid; grid-template-rows:auto minmax(0,1fr); min-width:0; min-height:0; border-right:1px solid #dde5e2; background:#f5f7f6; }
.version-navigation__groups { padding:14px; border-bottom:1px solid #dde5e2; }
.version-navigation__groups :deep(.el-segmented) { width:100%; }
.desktop-version-body { position:relative; min-height:0; overflow:hidden; }
.version-navigation__error { margin:12px; width:auto; }
.desktop-version-body :deep(.el-empty) { padding:42px 12px; }
.version-list { display:grid; align-content:start; gap:2px; height:100%; padding:8px; overflow-y:auto; scrollbar-gutter:stable; }
.version-list-item { position:relative; display:grid; gap:7px; width:100%; min-height:68px; padding:10px 11px 10px 13px; border:0; border-left:3px solid transparent; border-radius:4px; color:#344943; background:transparent; text-align:left; cursor:pointer; transition:background-color .16s ease,border-color .16s ease; }
.version-list-item:hover { background:#fff; }
.version-list-item:focus-visible { outline:2px solid #2d776c; outline-offset:-2px; }
.version-list-item.active { border-left-color:#2d776c; background:#fff; }
.version-list-item__topline,.version-list-item__meta { display:flex; align-items:center; justify-content:space-between; gap:10px; min-width:0; }
.version-list-item__topline strong { color:#245f56; font:700 15px "Cascadia Mono",monospace; }
.version-list-item__topline small { padding:1px 5px; border-radius:3px; color:#50645e; background:#e4ece9; font-size:12px; }
.version-list-item__meta b { overflow:hidden; color:#354b45; font-size:13px; font-weight:650; text-overflow:ellipsis; white-space:nowrap; }
.version-list-item__meta time { flex:0 0 auto; color:#778680; font:12px "Cascadia Mono",monospace; }
.mobile-version-body { display:none; }
.mobile-version-option { display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%; }
.mobile-version-option strong { color:#245f56; font-family:"Cascadia Mono",monospace; }
.mobile-version-option span { overflow:hidden; color:#6f7f7a; text-overflow:ellipsis; white-space:nowrap; }
@media (max-width:760px) {
  .version-navigation { grid-template-rows:auto auto; border-right:0; border-bottom:1px solid #dde5e2; }
  .version-navigation__groups { padding:10px 14px 8px; border-bottom:0; }
  .desktop-version-body { display:none; }
  .mobile-version-body { display:block; padding:0 14px 10px; }
  .mobile-version-select { width:100%; }
  .version-navigation__error { margin:0; }
}
@media (prefers-reduced-motion:reduce) { .version-list-item { transition:none; } }
</style>
