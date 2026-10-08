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

/** 页内锚点导航（B 级 · 2026-09-27）：数据来自 registry 的 `sections`，由本容器统一渲染。
 *  未登记的模块返回空数组 ⇒ `v-if="sections.length"` 不渲染 nav，对其余模块零影响。 */
const sections = computed<{ id: string; label: string }[]>(() => moduleMeta.value?.sections ?? [])

/** 页内跳转：✅ 用 `scrollIntoView`（它会自动找到最近的滚动祖先）。
 *  本项目列表页的滚动宿主是 naive-ui 的 `.n-layout-scroll-container`，但详情页实测 `scrollIntoView`
 *  直接有效 —— 早期工程附录写「对它无效、必须自己算 scrollTop 再赋值」是**不实**（2026-09-27 复核）。
 *  ⚠️ `?.` 在 id 缺失时会**静默短路**：点击无任何反应、控制台零报错。这是最难发现的一类失败，
 *     故锚点 id 清单登记在 registry，由 `_scratch/_verify_anchors*.{py,js}` 自动断言守护。 */
function goSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

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
        <div class="am-head-row">
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
          <!-- 页内锚点导航（B 级 · 2026-09-27）：**由 shell 统一渲染**，数据源是 registry 的
               `sections` 字段。此前它是货币流动性页模板里独立占一行的一条 nav（23px 行高 + 10px
               间距）；挪到标题行右侧是为了用上长期空置的空间（实测标题行内容仅占 231px / 内容区
               1174px，右侧空置 943px），合并后行宽约 715px。
               ⚠️ 为什么不让子视图自己投递进来（Teleport）：2026-09-27 实测**否决** —— 本 NCard 是
                  `v-if="moduleMeta"`，切换模块时先卸载、target 随 DOM 消失，子组件的 Teleport
                  卸载再去操作失效节点抛 `Cannot set properties of null (setting '__vnode')`，
                  **导致切回后详情页整页不渲染**（kpiCards=0）。
               ⚠️ 只有登记了 `sections` 的模块才渲染 ⇒ 其余 4 个分析模块零影响（实测标题行高度不变）。
               ⚠️ id 缺失时 `?.` 会**静默短路**：点击毫无反应、控制台零报错。故清单登记在 registry，
                  由 `_scratch/_verify_anchors.py` 纳入自动断言（此前这 5 个 id 无任何自动覆盖）。 -->
          <nav v-if="sections.length" class="am-nav">
            <span class="am-nav-label">直达</span>
            <button
              v-for="s in sections"
              :key="s.id"
              type="button"
              class="am-nav-item"
              @click="goSection(s.id)"
            >{{ s.label }}</button>
          </nav>
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
/* 标题行（2026-09-27 由块级改 flex）：左段「返回 / 标题 / 标签」 + 右段「页内导航」。
   原先 .am-head-left 是块级、独占一行，导航只能另起一行占 23px + 10px 间距 ⇒ 合并后整行省掉。 */
.am-head-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
/* min-width: 0 让左段在窄屏可收缩，优先保住标题不被导航挤出容器 */
.am-head-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.am-title { margin: 0; font-size: 18px; color: #1F2937; }
/* 页内导航：仅登记了 registry.sections 的模块渲染（其余模块零影响）。靠 margin-left:auto 推到
   标题行右端；放不下时整体换到第二行（flex-wrap 兜底），不挤压标题。样式与迁移前的页内导航一致。 */
.am-nav { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-left: auto; }
.am-nav-label { font-size: 11px; color: #9CA3AF; }
.am-nav-item {
  font-size: 12px; color: #185FA5; background: #F3F4F5; border: 1px solid #E5E7EB;
  border-radius: 12px; padding: 2px 10px; cursor: pointer; font-family: inherit;
}
.am-nav-item:hover { background: #EAF2FB; border-color: #C7DDF2; }
.am-desc { font-size: 12px; color: #9CA3AF; margin-top: 6px; }
</style>
