import axios from 'axios'
import { readonly, ref } from 'vue'
import { aiRequestId, cancelAiRequestApi } from '@/api/employeeAi'

export interface CancelableAiContext {
  requestId: string
  signal: AbortSignal
}

export function isAiCancellation(error: unknown) {
  return axios.isCancel(error)
    || (error instanceof Error && ('code' in error && error.code === 'ERR_CANCELED'))
}

export function useCancelableAiRequest() {
  const running = ref(false)
  let generation = 0
  let active: { requestId: string; controller: AbortController; generation: number } | undefined

  async function run<T>(operation: (context: CancelableAiContext) => Promise<T>): Promise<T | undefined> {
    if (active) return undefined
    const requestId = aiRequestId()
    const controller = new AbortController()
    const current = { requestId, controller, generation: ++generation }
    active = current
    running.value = true
    try {
      const result = await operation({ requestId, signal: controller.signal })
      return current.generation === generation ? result : undefined
    } catch (error) {
      if (isAiCancellation(error) || current.generation !== generation) return undefined
      throw error
    } finally {
      if (active === current) {
        active = undefined
        running.value = false
      }
    }
  }

  async function stop(): Promise<boolean> {
    const current = active
    if (!current) return true
    active = undefined
    generation += 1
    running.value = false
    current.controller.abort()
    try {
      await cancelAiRequestApi(current.requestId)
      return true
    } catch {
      return false
    }
  }

  return { running: readonly(running), run, stop }
}
