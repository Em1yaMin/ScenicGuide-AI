<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

/** 用户名规则（对齐后端 RegisterRequest：3-32 位字母/数字/下划线）。 */
const USERNAME_PATTERN = /^[a-zA-Z0-9_]{3,32}$/
const PASSWORD_MIN = 6
const PASSWORD_MAX = 64
const NICKNAME_MAX = 32

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const nickname = ref('')
const formError = ref<string | null>(null)
const isSubmitting = ref(false)

const redirectTarget = computed(() => {
  const raw = route.query.redirect
  if (typeof raw === 'string' && raw.startsWith('/') && !raw.startsWith('//')) {
    return raw
  }
  return '/visitor'
})

function validateForm(): string | null {
  const name = username.value.trim()
  if (!name) return '请输入用户名'
  if (!USERNAME_PATTERN.test(name)) {
    return '用户名需为 3-32 位字母、数字或下划线'
  }
  if (!password.value) return '请输入密码'
  if (password.value.length < PASSWORD_MIN) {
    return `密码长度至少 ${PASSWORD_MIN} 位`
  }
  if (password.value.length > PASSWORD_MAX) {
    return `密码长度不能超过 ${PASSWORD_MAX} 位`
  }
  if (!confirmPassword.value) return '请再次输入密码'
  if (password.value !== confirmPassword.value) return '两次输入的密码不一致'
  if (nickname.value.trim().length > NICKNAME_MAX) {
    return `昵称长度不能超过 ${NICKNAME_MAX} 位`
  }
  return null
}

async function handleRegister() {
  formError.value = null
  const error = validateForm()
  if (error) {
    formError.value = error
    return
  }
  isSubmitting.value = true
  try {
    // 注册成功后自动登录（后端注册接口不返回 token）
    const trimmedNickname = nickname.value.trim()
    await auth.register({
      username: username.value.trim(),
      password: password.value,
      ...(trimmedNickname ? { nickname: trimmedNickname } : {}),
    })
    router.replace(redirectTarget.value)
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '注册失败，请稍后重试'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-brand">
        <span class="brand-icon">⛰</span>
        <span class="brand-text">景区导览 AI 数字人</span>
      </div>
      <h1 class="auth-title">注册</h1>
      <p class="auth-desc">创建账号，登录后开启长期记忆</p>

      <form class="auth-form" @submit.prevent="handleRegister">
        <div class="form-row">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            type="text"
            name="username"
            autocomplete="username"
            placeholder="3-32 位字母、数字或下划线"
          />
        </div>
        <div class="form-row">
          <label for="nickname">昵称（可选）</label>
          <input
            id="nickname"
            v-model="nickname"
            type="text"
            name="nickname"
            autocomplete="nickname"
            placeholder="展示用昵称，不超过 32 位"
          />
        </div>
        <div class="form-row">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            name="password"
            autocomplete="new-password"
            placeholder="至少 6 位"
          />
        </div>
        <div class="form-row">
          <label for="confirm-password">确认密码</label>
          <input
            id="confirm-password"
            v-model="confirmPassword"
            type="password"
            name="confirm-password"
            autocomplete="new-password"
            placeholder="请再次输入密码"
          />
        </div>

        <p v-if="formError" class="form-error">{{ formError }}</p>

        <button type="submit" class="btn-primary" :disabled="isSubmitting">
          {{ isSubmitting ? '注册中…' : '注册并登录' }}
        </button>
      </form>

      <p class="auth-switch">
        已有账号？
        <RouterLink :to="{ path: '/login', query: route.query }">去登录</RouterLink>
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

.btn-primary {
  width: 100%;
  padding: var(--space-sm) var(--space-lg);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: var(--text-body);
  font-weight: 500;
  background: var(--color-ink);
  color: var(--color-paper);
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.btn-primary:hover {
  background: var(--color-accent);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
