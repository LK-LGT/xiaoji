<template>
  <div class="pan-workload-page">
    <!--
      ⚠️ **按角色渲染**（2026-11-24）

      ⚠️ **为什么不能只按权限位切**
      过去的切法是「有没有 `pan:workload:resolve`」⇒ 有就显示主管面板。
      ⇒ 但：
      - **剪辑**（reporter）该看到的是「我的填报 + 我的数据」
      - **主管**（reviewer）该看到的是「按人汇总 + 待裁定工单」+ **自己也要填比率**
      - **主播**（none）根本不该进这个页面（菜单已被过滤）

      ⇒ 现在按**后端下发的岗位角色**切 ⇒ 与飞书端口径完全一致。
    -->
    <template v-if="canResolve">
      <PanWorkloadPanel :period="period" can-resolve @employee-selected="onEmployeeSelected" />
      <el-divider content-position="left">
        <span class="divider-text">我的填报与异议</span>
      </el-divider>
      <PanWorkloadEntry ref="entryRef" />
    </template>
    <template v-else>
      <PanWorkloadEntry ref="entryRef" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import PanWorkloadEntry from './PanWorkloadEntry.vue'
import PanWorkloadPanel from './PanWorkloadPanel.vue'
import { useAuthStore } from '@/stores/auth'
import { hasPermission } from '@/navigation/workspaces'

const auth = useAuthStore()
const entryRef = ref<InstanceType<typeof PanWorkloadEntry> | null>(null)

/** 当前周期。 */
const period = computed(() => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
})

/**
 * ⚠️ **能否裁定** —— 前端权限位只管「按钮显不显示」，
 * **不是安全措施**（后端 `_require_leader` 才是）。
 */
const canResolve = computed(() =>
  hasPermission(auth.user?.permissions || [], 'pan:workload:resolve'),
)

/** 主管点了某人的「明细」⇒ 员工侧切到那个人。 */
function onEmployeeSelected(employeeId: string) {
  // ⚠️ 员工侧是「我的」视角 —— 主管点别人时没有对应的员工侧界面，
  //    所以这里只提示，不做跨人切换（避免做出「冒充员工」的界面）。
  void employeeId
}
</script>

<style scoped>
.pan-workload-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.divider-text {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
