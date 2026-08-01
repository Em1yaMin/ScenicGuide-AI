import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { API_VISITOR_REGISTER } from '@/api'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { generateUUID } from '@/utils/uuid'

const STORAGE_KEY = 'visitor_id'

export const useVisitorStore = defineStore('visitor', () => {
  const visitorId = ref<string>(localStorage.getItem(STORAGE_KEY) ?? generateUUID())
  localStorage.setItem(STORAGE_KEY, visitorId.value)

  const isAuthenticated = computed(() => Boolean(visitorId.value))

  /**
   * 将前端生成的访客 UUID 注册到后端。
   * 注册为非关键操作——失败时静默忽略，不阻塞应用。
   */
  async function registerVisitor(): Promise<void> {
    try {
      await fetchWithCodeEnvelope(API_VISITOR_REGISTER, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visitor_id: visitorId.value }),
      })
    } catch {
      // Registration is non-critical — silently ignore failures
    }
  }

  return { visitorId, isAuthenticated, registerVisitor }
})
