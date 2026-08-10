/**
 * 管理员认证 Pinia store（批次9：账号密码登录，替代 X-Admin-Token 输入形式）。
 *
 * - 登录成功后 JWT 与账号持久化到 localStorage（admin_jwt / admin_username）
 * - 管理端路由守卫（router beforeEach）依据 isLoggedIn 拦截未登录访问 /admin
 * - 退出登录清除本地凭据（JWT 无状态，不调用后端）
 * - 兼容旧用法：管理端 API 请求头同时携带 JWT 与 X-Admin-Token（见
 *   utils/adminHeaders.ts，向后兼容不破坏既有部署）
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { adminLogin } from '@/api/adminAuth'
import type { AdminLoginPayload } from '@/types/adminAuth'
import { FetchError } from '@/utils/fetchWithCodeEnvelope'

const TOKEN_KEY = 'admin_jwt'
const USERNAME_KEY = 'admin_username'

/** 将错误归一化为友好消息：优先后端 detail（如 401「用户名或密码错误」）。 */
function friendlyError(e: unknown, fallback: string): string {
  if (e instanceof FetchError) {
    const generic = e.httpStatus ? `服务器返回错误 (${e.httpStatus})` : ''
    if (e.message && e.message !== generic) return e.message
    return fallback
  }
  return e instanceof Error && e.message ? e.message : fallback
}

export const useAdminAuthStore = defineStore('adminAuth', () => {
  const jwt = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const username = ref<string | null>(localStorage.getItem(USERNAME_KEY))

  /** 已登录（持有管理员 JWT）。 */
  const isLoggedIn = computed(() => Boolean(jwt.value))

  /** 登录：成功后持久化 JWT + 账号。失败抛出友好错误消息。 */
  async function login(payload: AdminLoginPayload): Promise<string> {
    let result
    try {
      result = await adminLogin(payload)
    } catch (e) {
      throw new Error(friendlyError(e, '管理员账号或密码错误'))
    }
    jwt.value = result.access_token
    username.value = result.username
    localStorage.setItem(TOKEN_KEY, result.access_token)
    localStorage.setItem(USERNAME_KEY, result.username)
    return result.username
  }

  /** 退出登录：清除本地管理员凭据（JWT 无状态）。 */
  function logout() {
    jwt.value = null
    username.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USERNAME_KEY)
  }

  return { jwt, username, isLoggedIn, login, logout }
})
