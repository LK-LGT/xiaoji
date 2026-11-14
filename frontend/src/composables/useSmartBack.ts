import { computed, unref, type MaybeRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fallbackForPath, isUsableBackPath } from '@/navigation/smartBack'

export function useSmartBack(fallback?: MaybeRef<string | undefined>) {
  const route = useRoute()
  const router = useRouter()

  const backPath = computed(() => window.history.state?.back)
  const fallbackPath = computed(() => unref(fallback) || fallbackForPath(route.path))
  const canGoBack = computed(() =>
    isUsableBackPath(backPath.value, route.fullPath) || fallbackPath.value !== route.path)

  async function goBack(targetFallback?: string) {
    const stateBack = window.history.state?.back
    if (isUsableBackPath(stateBack, route.fullPath)) {
      router.back()
      return
    }
    const target = targetFallback || fallbackPath.value
    if (target && target !== route.fullPath) await router.push(target)
  }

  return { canGoBack, fallbackPath, goBack }
}
