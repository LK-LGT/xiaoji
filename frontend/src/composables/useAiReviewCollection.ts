import { ref } from 'vue'
import {
  getLatestAiReviewApi,
  getLatestAiReviewsApi,
  type AiReview,
  type AiReviewBizType,
} from '@/api/aiReview'

/**
 * BUG-R6-14：业务对象 ID（计划 / 成果主键）是字符串 UUID，
 * 这里一律按字符串处理，禁止 `Number(...)` 归一化，
 * 否则十六进制 / UUID 会被折叠为 `NaN` / `0`。
 */
function normalizeId(rawId: number | string | null | undefined): string {
  if (rawId === null || rawId === undefined) return ''
  return String(rawId).trim()
}

export function useAiReviewCollection(bizType: AiReviewBizType) {
  const reviews = ref<Record<string, AiReview>>({})
  const loading = ref(false)
  let requestId = 0

  async function load(rawIds: Array<number | string>) {
    const activeRequest = ++requestId
    const ids = [...new Set(rawIds.map(normalizeId).filter((id) => id.length > 0))]
    if (!ids.length) {
      reviews.value = {}
      return
    }
    loading.value = true
    try {
      const chunks = Array.from({ length: Math.ceil(ids.length / 100) }, (_, index) => ids.slice(index * 100, (index + 1) * 100))
      let result: Record<string, AiReview>
      try {
        result = Object.assign({}, ...await Promise.all(chunks.map((chunk) => getLatestAiReviewsApi(bizType, chunk))))
      } catch {
        const entries = await Promise.all(ids.map(async (id) => {
          try {
            const review = await getLatestAiReviewApi(bizType, id)
            return review ? [id, review] as const : null
          } catch {
            return null
          }
        }))
        result = Object.fromEntries(entries.filter((entry): entry is readonly [string, AiReview] => entry !== null))
      }
      if (activeRequest === requestId) reviews.value = result
    } catch {
      if (activeRequest === requestId) reviews.value = {}
    } finally {
      if (activeRequest === requestId) loading.value = false
    }
  }

  async function loadOne(rawId: number | string) {
    const id = normalizeId(rawId)
    if (!id) return null
    try {
      const review = await getLatestAiReviewApi(bizType, id)
      if (review) reviews.value = { ...reviews.value, [id]: review }
      else if (reviews.value[id]) {
        const next = { ...reviews.value }
        delete next[id]
        reviews.value = next
      }
      return review
    } catch {
      return reviews.value[id] || null
    }
  }

  function reviewFor(rawId: number | string | null | undefined) {
    const id = normalizeId(rawId)
    return id ? reviews.value[id] || null : null
  }

  return { reviews, loading, load, loadOne, reviewFor }
}
