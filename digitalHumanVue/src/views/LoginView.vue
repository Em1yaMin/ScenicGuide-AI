<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const formError = ref<string | null>(null)
const isSubmitting = ref(false)
const isEnteringGuest = ref(false)

/** 登录成功后的跳转目标：优先 redirect 参数（仅允许站内路径），默认访客首页。 */
const redirectTarget = computed(() => {
  const raw = route.query.redirect
  if (typeof raw === 'string' && raw.startsWith('/') && !raw.startsWith('//')) {
    return raw
  }
  return '/visitor'
})

async function handleLogin() {
  if (isSubmitting.value) return
  formError.value = null
  const name = username.value.trim()
  if (!name) {
    formError.value = '请输入用户名'
    return
  }
  if (!password.value) {
    formError.value = '请输入密码'
    return
  }
  isSubmitting.value = true
  try {
    await auth.login({ username: name, password: password.value })
    router.replace(redirectTarget.value)
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '登录失败，请稍后重试'
  } finally {
    isSubmitting.value = false
  }
}

async function enterAsGuest() {
  if (isEnteringGuest.value) return
  isEnteringGuest.value = true
  formError.value = null
  // 游客 token 签发失败（如后端未就绪）时静默降级：仍以游客身份进入
  await auth.enterGuestMode()
  router.replace(redirectTarget.value)
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-brand">
        <span class="brand-icon">⛰</span>
        <span class="brand-text">景区导览 AI 数字人</span>
      </div>
      <h1 class="auth-title">登录</h1>
      <p class="auth-desc">登录后开启长期记忆，享受个性化导览</p>

      <form class="auth-form" @submit.prevent="handleLogin">
        <div class="form-row">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            type="text"
            name="username"
            autocomplete="username"
            placeholder="请输入用户名"
            @keydown.enter="handleLogin"
          />
        </div>
        <div class="form-row">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            name="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            @keydown.enter="handleLogin"
          />
        </div>

        <p v-if="formError" class="form-error">{{ formError }}</p>

        <button type="submit" class="btn-primary" :disabled="isSubmitting">
          {{ isSubmitting ? '登录中…' : '登录' }}
        </button>
      </form>

      <template v-if="!auth.isLoggedIn">
        <div class="auth-divider">
          <span>或</span>
        </div>

        <button
          type="button"
          class="btn-secondary"
          :disabled="isEnteringGuest"
          @click="enterAsGuest"
        >
          {{ isEnteringGuest ? '进入中…' : '游客模式直接进入' }}
        </button>
      </template>

      <p class="auth-switch">
        还没有账号？
        <RouterLink :to="{ path: '/register', query: route.query }">立即注册</RouterLink>
      </p>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-lg);
  color: var(--color-ink);
  background: var(--color-paper);
}

.auth-card {
  width: 100%;
  max-width: 400px;
  padding: var(--space-xl);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-paper);
}

.auth-brand {
  display: flex;
  align-items: baseline;
  gap: var(--space-xs);
  margin-bottom: var(--space-xl);
}

.brand-icon {
  font-size: var(--text-title);
  line-height: 1;
}

.brand-text {
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--color-ink);
}

.auth-title {
  font-family: var(--font-display);
  font-size: var(--text-display);
  margin: 0 0 var(--space-xs);
  color: var(--color-ink);
}

.auth-desc {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  margin: 0 0 var(--space-xl);
}

.form-row {
  margin-bottom: var(--space-md);
}

.form-row label {
  display: block;
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  margin-bottom: var(--space-xs);
}

.form-row input {
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: var(--text-body);
  background: var(--color-paper);
  color: var(--color-ink);
  box-sizing: border-box;
}

.form-row input:focus {
  outline: none;
  border-color: var(--color-ink-secondary);
}

.form-error {
  color: var(--color-accent);
  font-size: var(--text-small);
  margin: 0 0 var(--space-md);
}

.btn-primary,
.btn-secondary {
  width: 100%;
  padding: var(--space-sm) var(--space-lg);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: var(--text-body);
  font-weight: 500;
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.btn-primary {
  background: var(--color-ink);
  color: var(--color-paper);
}

.btn-primary:hover {
  background: var(--color-accent);
}

.btn-primary:disabled,
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  background: transparent;
  color: var(--color-ink);
  border: 1px solid var(--color-border);
}

.btn-secondary:hover {
  background: var(--color-overlay);
}

.auth-divider {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  margin: var(--space-lg) 0;
  color: var(--color-ink-secondary);
  font-size: var(--text-caption);
}

.auth-divider::before,
.auth-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--color-border);
}

.auth-switch {
  text-align: center;
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  margin: var(--space-lg) 0 0;
}

.auth-switch a {
  color: var(--color-accent);
  text-decoration: none;
}

.auth-switch a:hover {
  text-decoration: underline;
}
</style>
