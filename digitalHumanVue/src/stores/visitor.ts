import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { API_VISITOR_REGISTER } from '@/api'

const STORAGE_KEY = 'visitor_id'

export const useVisitorStore = defineStore('visitor', () => {
  const visitorId = ref<string>(localStorage.getItem(STORAGE_KEY) ?? crypto.randomUUID())
  localStorage.setItem(STORAGE_KEY, visitorId.value)

  const isAuthenticated = computed(() => Boolean(visitorId.value))

  async function createVisitor(): Promise<boolean> {
    try {
      const response = await fetch(API_VISITOR_REGISTER, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visitor_id: visitorId.value }),
      })
      const result = await response.json()
      if (result.code) {
        console.error('Failed to register visitor:', result.message)
        return false
      }
      return true
    } catch (error) {
      console.error('Failed to register visitor:', error)
      return false
    }
  }

  return { visitorId, isAuthenticated, createVisitor }
})
