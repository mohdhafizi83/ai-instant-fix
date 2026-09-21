<!--
  AI Instant Fix — Vue 3 variant (SFC)

  Usage:
    <script setup>
      import AiInstantFix from './AiInstantFix.vue'
    </script>
    <template>
      <AiInstantFix api="https://fix.example.com" userId="admin" :token="jwt" theme="#247b70" />
    </template>

  No npm package needed — copy this file into your project.
-->
<script setup>
import { onMounted, ref } from 'vue'

const props = defineProps({
  api: { type: String, required: true },
  userId: { type: String, default: 'anonymous' },
  token: { type: String, default: '' },
  theme: { type: String, default: '#247b70' },
})

const loaded = ref(false)

onMounted(() => {
  if (loaded.value || window.__AIF_LOADED__ || !props.api) return
  loaded.value = true

  const s = document.createElement('script')
  s.src = `${props.api.replace(/\/+$/, '')}/widget/ai-instant-fix.js`
  s.dataset.aifApi = props.api
  s.dataset.aifUserId = props.userId
  if (props.token) s.dataset.aifToken = props.token
  if (props.theme) s.dataset.aifTheme = props.theme
  document.body.appendChild(s)
})
</script>

<template>
  <!-- Widget renders itself into document.body; this component renders nothing. -->
</template>
