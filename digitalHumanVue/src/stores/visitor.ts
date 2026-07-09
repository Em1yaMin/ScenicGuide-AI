import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

const STORAGE_KEY = 'visitor_id'

export const useVisitorStore = defineStore('visitor', () => {
  const visitorId = ref<string>(localStorage.getItem(STORAGE_KEY) ?? crypto.randomUUID())
  localStorage.setItem(STORAGE_KEY, visitorId.value)

  const isAuthenticated = computed(() => Boolean(visitorId.value))

  async function createVisitor(): Promise<boolean> {
    try {
      const response = await fetch('/api/v1/visitor/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visitor_id: visitorId.value }),
      })
      if (!response.ok) {
        console.error('Failed to register visitor', response.status)
        return false
      }
      return true
    } catch (error) {
      console.error('Failed to register visitor', error)
      return false
    }
  }

  return { visitorId, isAuthenticated, createVisitor }
})
