import { computed, ref } from 'vue'
import { getLeaderOrgTreeApi } from '@/api/leader'
import type { OrgNode } from '@/api/performance'

interface LeaderOrgOption {
  value: string
  label: string
}

function flattenOrgTree(nodes: OrgNode[], parents: string[] = []): LeaderOrgOption[] {
  return nodes.flatMap((node) => {
    const path = [...parents, node.label]
    return [
      { value: node.id, label: path.join(' / ') },
      ...flattenOrgTree(node.children || [], path),
    ]
  })
}

export function useLeaderOrgScope(selectFirstByDefault = false) {
  const orgTree = ref<OrgNode[]>([])
  const scopeOrgId = ref<string>()
  const orgLoading = ref(false)
  const orgOptions = computed(() => flattenOrgTree(orgTree.value))

  async function loadOrgScope() {
    orgLoading.value = true
    try {
      orgTree.value = await getLeaderOrgTreeApi()
      if (!orgOptions.value.some((item) => item.value === scopeOrgId.value)) {
        scopeOrgId.value = selectFirstByDefault ? orgOptions.value[0]?.value : undefined
      }
      return scopeOrgId.value
    } finally {
      orgLoading.value = false
    }
  }

  function resetOrgScope() {
    scopeOrgId.value = selectFirstByDefault ? orgOptions.value[0]?.value : undefined
  }

  return { orgTree, orgOptions, scopeOrgId, orgLoading, loadOrgScope, resetOrgScope }
}
