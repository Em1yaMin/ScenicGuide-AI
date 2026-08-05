<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

// 应用启动时依据 localStorage token 恢复登录态（后端未就绪/网络失败时静默保留本地缓存）
const auth = useAuthStore()

onMounted(() => {
  void auth.restoreSession()
})
</script>

<template>
  <RouterView />
</template>
