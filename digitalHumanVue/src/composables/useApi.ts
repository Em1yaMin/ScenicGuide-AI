import { ref, type Ref } from 'vue'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

export interface UseApiReturn<T> {
  data: Ref<T | null>
  isLoading: Ref<boolean>
  error: Ref<string | null>
  execute: () => Promise<void>
}

/**
 * 数据获取 composable。封装三态状态机（loading / error / data）。
 *
 * @param url — API 端点 URL（来自 @/api 常量）
 * @returns { data, isLoading, error, execute }
 *
 * isLoading 初始值为 true（匹配视图三态模式：挂载时即显示加载状态）。
 */
export function useApi<T>(url: string): UseApiReturn<T> {
  const data = ref<T | null>(null) as Ref<T | null>
  const isLoading = ref(true)
  const error = ref<string | null>(null)

  async function execute(): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      data.value = await fetchWithCodeEnvelope<T>(url)
    } catch (e) {
      error.value = e instanceof Error ? e.message : '未知错误'
    } finally {
      isLoading.value = false
    }
  }

  return { data, isLoading, error, execute }
}
