<script setup lang="ts">
/**
 * AnalysisModuleView —— 分析模块详情容器（AnalysisShell v0.2）
 * 路由 /analysis/:moduleId；标题区（名称/口径/分组/截至）由容器统一渲染，主视图由模块组件注入
 * 模块注册：后端 app/analysis/registry.py；前端组件映射见本文件下方 moduleViews（此前注释指向
 * 已不存在的 src/analysis/modules.ts，2026-09-14 订正）
 */
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NEmpty, NSpin, NTag } from 'naive-ui'
import RichText from '../components/analysis/RichText.vue'
import api from '../api'

const route = useRoute()
const router = useRouter()
const moduleId = computed(() => String(route.params.moduleId ?? ''))

const loading = ref(false)
const moduleMeta = ref<any>(null)

// moduleId → 视图组件映射（新增分析模块在此登记一行；后端元数据在 app/analysis/registry.py）
// ⚠️ 必须用 defineAsyncComponent 包装：裸 () => import() 会被 Vue 当作「函数式组件」调用，
//    返回的 Promise 会被直接渲染成 "[object Promise]"，模块主体不渲染（2026-09-14 实测踩坑）
const moduleViews: Record<string, any> = {
  'market-wind': defineAsyncComponent(() => import('./analysis/MarketWindView.vue')),
  'sector-rotation': defineAsyncComponent(() => import('./analysis/SectorRotationView.vue')),
  // Batch C（2026-09-19）：把三张"只进不出"的表接上消费端
  'money-cost': defineAsyncComponent(() => import('./analysis/MoneyCostView.vue')),
  'cross-market': defineAsyncComponent(() => import('./analysis/CrossMarketView.vue')),
  'funding-temperature': defineAsyncComponent(() => import('./analysis/FundingTemperatureView.vue')),
}
const viewComp = computed(() => moduleViews[moduleId.value])

// ⚠️ 必须 watch(moduleId, { immediate: true }) 而不是 onMounted：
//    本组件对 /analysis/:moduleId 是**同一个路由记录**，模块间切换时 vue-router 复用实例、
//    不重新挂载 → onMounted 不会重跑 → 头部名称/口径/分组会残留上一个模块（2026-09-15 实测截图发现）。
watch(moduleId, async (id) => {
  moduleMeta.value = null
  if (!id) return
  loading.value = true
  try {
    const resp: any = await api.get('/analysis/registry')
    moduleMeta.value = (resp.items ?? []).find((m: any) => m.module_id === id) ?? null
  } catch (e) {
    console.error('[analysis-registry]', e)
  } finally {
    loading.value = false
  }
}, { immediate: true })
</script>

<template>
  <NSpin :show="loading">
    <NCard v-if="moduleMeta" size="small" class="am-shell">
      <div class="am-head">
        <div class="am-head-left">
          <NButton size="tiny" quaternary @click="router.push('/analysis')">← 分析研究</NButton>
          <h2 class="am-title">{{ moduleMeta.icon }} {{ moduleMeta.name }}</h2>
          <!-- 领域标签：只在「模块 ≠ 所属领域」时渲染，即 track 卡页（如 /analysis/market-wind-position
               → 显示「市场风向」）。detail 页自身即领域，再打同一张标签只是与标题重复。
               ⚠️ 改前无条件渲染，且 group 是另一套分类 ⇒ 跨市场对照页顶着「市场风向」（2026-09-25 修）。 -->
          <NTag
            v-if="moduleMeta.group && moduleMeta.group !== moduleMeta.name"
            size="small"
            :bordered="false"
            type="info"
          >{{ moduleMeta.group }}</NTag>
          <NTag size="small" :bordered="false">{{ moduleMeta.kind === 'track' ? '跟踪' : '研究' }}</NTag>
        </div>
        <div class="am-desc">
          <RichText :text="moduleMeta.desc" />
          <span v-if="moduleMeta.schedule_text || moduleMeta.updated_cron">
            · 更新：{{ moduleMeta.schedule_text || moduleMeta.updated_cron }}</span>
        </div>
      </div>
      <component v-if="viewComp" :is="viewComp" />
      <NEmpty v-else description="模块视图开发中…" style="padding: 40px 0" />
    </NCard>
    <NCard v-else-if="!loading" size="small">
      <NEmpty description="未找到该分析模块">
        <template #extra>
          <NButton size="small" @click="router.push('/analysis')">返回分析研究</NButton>
        </template>
      </NEmpty>
    </NCard>
  </NSpin>
</template>

<style scoped>
.am-head { margin-bottom: 14px; }
.am-head-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.am-title { margin: 0; font-size: 18px; color: #1F2937; }
.am-desc { font-size: 12px; color: #9CA3AF; margin-top: 6px; }
</style>
