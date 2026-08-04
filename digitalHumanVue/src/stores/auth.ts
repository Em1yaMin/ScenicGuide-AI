/**
 * 用户认证 Pinia store（setup 语法，对齐 stores/visitor.ts 风格）。
 *
 * - token 与用户信息持久化到 localStorage（键名沿用 snake_case 风格：
 *   `visitor_id` / `admin_api_token` → `auth_token` / `auth_user`）
 * - 游客兜底：未登录/游客 token 均可正常使用对话等公开能力；登录用户享后续长期记忆
 * - 页面刷新后由 App.vue 调用 restoreSession() 依据 localStorage token 恢复登录态
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { createGuestToken, fetchMe, loginUser, registerUser } from '@/api/auth'
import type {
  AuthResponse,
  GuestIdentity,
  LoginPayload,
  RegisterPayload,
  UserInfo,
} from '@/types/auth'
import { FetchError } from '@/utils/fetchWithCodeEnvelope'

const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

/** 从 localStorage 恢复用户信息（JSON 损坏时静默返回 null）。 */
function readStoredUser(): UserInfo | GuestIdentity | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as UserInfo | GuestIdentity
  } catch {
    return null
  }
}

/** 将错误归一化为友好消息：优先后端 detail，回退按状态码的兜底文案。 */
function friendlyError(e: unknown, fallback: string): string {
  if (e instanceof FetchError) {
    const generic = e.httpStatus ? `服务器返回错误 (${e.httpStatus})` : ''
    if (e.message && e.message !== generic) return e.message
    return fallback
  }
  return e instanceof Error && e.message ? e.message : fallback
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const user = ref<UserInfo | GuestIdentity | null>(readStoredUser())
  const isRestoring = ref(false)

  /** 已登录（用户 token，非游客）。 */
  const isLoggedIn = computed(() => {
    if (!token.value || !user.value) return false
    return !('is_guest' in user.value && user.value.is_guest)
  })

  /** 游客模式（持有短期游客 token）。 */
  const isGuest = computed(() => {
    if (!token.value || !user.value) return false
    return 'is_guest' in user.value && user.value.is_guest === true
  })

  /** 持有任意 token（登录用户或游客）。 */
  const isAuthenticated = computed(() => Boolean(token.value))

  /** 界面展示名：昵称 > 用户名 > 游客。 */
  const displayName = computed(() => {
    if (isLoggedIn.value) {
      const info = user.value as UserInfo
      return info.nickname || info.username
    }
    if (isGuest.value) return '游客'
    return ''
  })

  function persistSession(nextToken: string, nextUser: UserInfo | GuestIdentity) {
    token.value = nextToken
    user.value = nextUser
    localStorage.setItem(TOKEN_KEY, nextToken)
    localStorage.setItem(USER_KEY, JSON.stringify(nextUser))
  }

  function clearSession() {
    token.value = null
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  /** 登录：成功持久化 token + 用户信息。失败抛出友好错误消息。 */
  async function login(payload: LoginPayload): Promise<UserInfo> {
    let result: AuthResponse
    try {
      result = await loginUser(payload)
    } catch (e) {
      throw new Error(friendlyError(e, '用户名或密码错误'))
    }
    persistSession(result.access_token, result.user)
    return result.user
  }

  /** 注册成功后自动登录（注册接口不返回 token，需二次调用登录）。 */
  async function register(payload: RegisterPayload): Promise<UserInfo> {
    try {
      await registerUser(payload)
    } catch (e) {
      throw new Error(friendlyError(e, '注册失败，请稍后重试'))
    }
    return login({ username: payload.username, password: payload.password })
  }

  /** 游客模式：签发短期游客 token（匿名）。失败时静默降级（无 token 亦可使用）。 */
  async function enterGuestMode(): Promise<boolean> {
    try {
      const result = await createGuestToken()
      persistSession(result.access_token, { id: result.guest_id, is_guest: true })
      return true
    } catch {
      clearSession()
      return false
    }
  }

  /** 退出登录：清除本地 token 与用户信息（不调用后端，JWT 无状态）。 */
  function logout() {
    clearSession()
  }

  /**
   * 页面刷新后恢复登录态：依据 localStorage token 调用 /auth/me 校验。
   * - 401（token 无效/过期）→ 清除本地登录态
   * - 网络失败等其它错误 → 保留本地缓存，不打断浏览（后端未就绪时前端可用）
   */
  async function restoreSession(): Promise<void> {
    const storedToken = localStorage.getItem(TOKEN_KEY)
    if (!storedToken) return
    token.value = storedToken
    user.value = readStoredUser()
    isRestoring.value = true
    try {
      const me = await fetchMe(storedToken)
      user.value = me
      localStorage.setItem(USER_KEY, JSON.stringify(me))
    } catch (e) {
      if (e instanceof FetchError && e.httpStatus === 401) {
        clearSession()
      }
    } finally {
      isRestoring.value = false
    }
  }

  /** 构造携带 Bearer token 的请求头（未登录时返回空对象，保持向后兼容）。 */
  function authHeaders(): Record<string, string> {
    return token.value ? { Authorization: `Bearer ${token.value}` } : {}
  }

  return {
    token,
    user,
    isRestoring,
    isLoggedIn,
    isGuest,
    isAuthenticated,
    displayName,
    login,
    register,
    enterGuestMode,
    logout,
    restoreSession,
    authHeaders,
  }
})
