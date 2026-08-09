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
  color: var(--ink);
  background: var(--paper);
}

.auth-card {
  width: 100%;
  max-width: 400px;
  padding: var(--space-xl);
  border: 1px solid var(--mist);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-card);
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
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ink);
}

.auth-title {
  font-family: var(--font-display);
  font-size: var(--text-display);
  font-weight: 700;
  margin: 0 0 var(--space-xs);
  color: var(--ink);
}

.auth-desc {
  font-size: var(--text-small);
  color: var(--slate);
  margin: 0 0 var(--space-xl);
}

.form-row {
  margin-bottom: var(--space-md);
}

.form-row label {
  display: block;
  font-size: var(--text-small);
  color: var(--slate);
  margin-bottom: var(--space-xs);
}

.form-row input {
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-body);
  background: var(--surface);
  color: var(--ink);
  box-sizing: border-box;
  transition: border-color var(--duration-fast) var(--ease-out);
}

.form-row input:focus {
  outline: none;
  border-color: var(--jade);
  box-shadow: 0 0 0 3px var(--jade-soft);
}

.form-error {
  color: var(--cinnabar);
  font-size: var(--text-small);
  margin: 0 0 var(--space-md);
}

.btn-primary,
.btn-secondary {
  width: 100%;
  padding: var(--space-sm) var(--space-lg);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-body);
  font-weight: 500;
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.btn-primary {
  background: var(--jade);
  color: var(--surface);
}

.btn-primary:hover {
  background: var(--jade-strong);
}

.btn-primary:disabled,
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  background: transparent;
  color: var(--ink);
  border: 1px solid var(--mist);
}

.btn-secondary:hover {
  background: var(--overlay);
  border-color: var(--mist-strong);
}

.auth-divider {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  margin: var(--space-lg) 0;
  color: var(--slate);
  font-size: var(--text-caption);
}

.auth-divider::before,
.auth-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--mist);
}

.auth-switch {
  text-align: center;
  font-size: var(--text-small);
  color: var(--slate);
  margin: var(--space-lg) 0 0;
}

.auth-switch a {
  color: var(--jade);
  text-decoration: none;
}

.auth-switch a:hover {
  text-decoration: underline;
}
</style>
