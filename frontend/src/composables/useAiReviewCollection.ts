import { ref } from 'vue'
import {
  getLatestAiReviewApi,
  getLatestAiReviewsApi,
  type AiReview,
  type AiReviewBizType,
} from '@/api/aiReview'

export function useAiReviewCollection(bizType: AiReviewBizType) {
  const reviews = ref<Record<number, AiReview>>({})
  const loading = ref(false)
  let requestId = 0

  async function load(rawIds: Array<number | string>) {
    const activeRequest = ++requestId
    const ids = [...new Set(rawIds.map(Number).filter((id) => Number.isInteger(id) && id > 0))]
    if (!ids.length) {
      reviews.value = {}
      return
    }
    loading.value = true
    try {
      const chunks = Array.from({ length: Math.ceil(ids.length / 100) }, (_, index) => ids.slice(index * 100, (index + 1) * 100))
      let result: Record<number, AiReview>
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
        result = Object.fromEntries(entries.filter((entry): entry is readonly [number, AiReview] => entry !== null))
      }
      if (activeRequest === requestId) reviews.value = result
    } catch {
      if (activeRequest === requestId) reviews.value = {}
    } finally {
      if (activeRequest === requestId) loading.value = false
    }
  }

  async function loadOne(rawId: number | string) {
    const id = Number(rawId)
    if (!Number.isInteger(id) || id <= 0) return null
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
    const id = Number(rawId)
    return Number.isInteger(id) && id > 0 ? reviews.value[id] || null : null
  }

  return { reviews, loading, load, loadOne, reviewFor }
}
