<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useAdminAuthStore } from '@/stores/adminAuth'

const route = useRoute()
const router = useRouter()
const adminAuth = useAdminAuthStore()

const username = ref('')
const password = ref('')
const formError = ref<string | null>(null)
const isSubmitting = ref(false)

/** 登录成功后的跳转目标：优先 redirect 参数（仅允许站内路径），默认管理后台首页。 */
const redirectTarget = computed(() => {
  const raw = route.query.redirect
  if (typeof raw === 'string' && raw.startsWith('/admin') && !raw.startsWith('//')) {
    return raw
  }
  return '/admin'
})

async function handleLogin() {
  if (isSubmitting.value) return
  formError.value = null
  const name = username.value.trim()
  if (!name) {
    formError.value = '请输入管理员账号'
    return
  }
  if (!password.value) {
    formError.value = '请输入密码'
    return
  }
  isSubmitting.value = true
  try {
    await adminAuth.login({ username: name, password: password.value })
    router.replace(redirectTarget.value)
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '登录失败，请稍后重试'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="admin-login">
    <!-- 页头返回入口（游客误入可随时退出回首页，批次修复） -->
    <div class="login-back">
      <RouterLink to="/" class="back-home-btn">← 返回首页</RouterLink>
    </div>

    <div class="login-card">
      <div class="login-brand">
        <span class="brand-seal">管</span>
        <span class="brand-text">景区管理后台</span>
      </div>
      <h1 class="login-title">管理员登录</h1>
      <p class="login-desc">
        管理员专用入口：使用管理员账号密码登录后进入管理后台，访客请返回首页使用数字人导览服务
      </p>

      <form class="login-form" @submit.prevent="handleLogin">
        <div class="form-row">
          <label for="admin-username">管理员账号</label>
          <input
            id="admin-username"
            v-model="username"
            type="text"
            name="username"
            autocomplete="username"
            placeholder="请输入管理员账号"
            @keydown.enter="handleLogin"
          />
        </div>
        <div class="form-row">
          <label for="admin-password">密码</label>
          <input
            id="admin-password"
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
          {{ isSubmitting ? '登录中…' : '进入管理后台' }}
        </button>
      </form>

      <p class="login-hint">
        默认演示账号见 README（环境变量 <code>ADMIN_USERNAME</code> / <code>ADMIN_PASSWORD</code>）
      </p>
    </div>
  </div>
</template>

<style scoped>
.admin-login {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-lg);
  color: var(--ink);
  background: var(--paper);
}

/* 页头返回入口：与登录卡片同宽、左对齐，描边按钮 token 规范（见 doc/redesign-v1.md §2.1 色彩） */
.login-back {
  width: 100%;
  max-width: 400px;
  margin-bottom: var(--space-md);
}

.back-home-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  padding: var(--space-xs) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--ink);
  font-size: var(--text-small);
  font-weight: 500;
  text-decoration: none;
  transition:
    background var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out);
}

.back-home-btn:hover {
  background: var(--overlay);
  border-color: var(--mist-strong);
  color: var(--jade);
}

.back-home-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--jade-soft);
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: var(--space-xl);
  border: 1px solid var(--mist);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-card);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-xl);
}

/* 朱砂印章式方块（母题同 AdminLayout 侧栏，见 doc/redesign-v1.md §4） */
.brand-seal {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  background: var(--cinnabar);
  color: var(--surface);
  font-family: var(--font-display);
  font-size: var(--text-small);
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
  transform: rotate(-2deg);
}

.brand-text {
  font-family: var(--font-display);
  font-size: var(--text-title);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ink);
}

.login-title {
  font-family: var(--font-display);
  font-size: var(--text-display);
  font-weight: 700;
  margin: 0 0 var(--space-xs);
  color: var(--ink);
}

.login-desc {
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

.btn-primary {
  width: 100%;
  padding: var(--space-sm) var(--space-lg);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-body);
  font-weight: 500;
  background: var(--jade);
  color: var(--surface);
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.btn-primary:hover {
  background: var(--jade-strong);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.login-hint {
  margin: var(--space-lg) 0 0;
  text-align: center;
  font-size: var(--text-caption);
  color: var(--slate);
}

.login-hint code {
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--slate);
  background: var(--overlay);
  padding: 1px 4px;
  border-radius: 4px;
}
</style>
