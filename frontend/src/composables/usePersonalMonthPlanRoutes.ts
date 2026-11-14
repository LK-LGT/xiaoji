import { computed } from 'vue'
import { useRoute } from 'vue-router'

const EMPLOYEE_BASE = '/employee/month-plans'
const LEADER_BASE = '/leader/my-month-plans'

export function usePersonalMonthPlanRoutes() {
  const route = useRoute()
  const isLeaderContext = computed(() => route.path.startsWith(LEADER_BASE))
  const basePath = computed(() => isLeaderContext.value ? LEADER_BASE : EMPLOYEE_BASE)

  return {
    isLeaderContext,
    basePath,
    listPath: () => basePath.value,
    editPath: (id?: number | string) => id == null
      ? `${basePath.value}/new/edit`
      : `${basePath.value}/${id}/edit`,
    aiPath: (id: number | string) => `${basePath.value}/${id}/ai`,
  }
}
