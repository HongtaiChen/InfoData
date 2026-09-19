<script setup lang="ts">
/**
 * AnalysisOverview —— 分析研究总览页（框架 v0.2）
 * 跟踪型模块 = 摘要卡片墙（关键信号 + 状态词，每天扫一眼）；研究型模块 = 分组目录
 * 模块来源：GET /api/analysis/registry（配置文件版注册表）
 */
import { computed, onMounted, ref } from 'vue'
import { NCard, NEmpty, NSkeleton, NSpin, NTag } from 'naive-ui'
import { useRouter } from 'vue-router'
import RichText from '../components/analysis/RichText.vue'
import api from '../api'

interface RegistryItem {
  module_id: string
  name: string
  group: string
  kind: 'track' | 'research'
  icon?: string
  desc: string
}
interface CardKpi {
  label: string
  value: number | null
  unit?: string
  status?: string
  tone?: 'updown' | 'neutral'
}

const GROUP_ORDER = ['市场风向', '板块与概念', '个股基本面', '资金与情绪', '跟踪清单']

const router = useRouter()
// loading 只表示「注册表本身」的加载——它是最轻的一次请求（实测 ~70ms）。
// 卡片区的 KPI 摘要另用 kpiLoading 逐卡标记，**不再阻塞整页**：
// 原先 loading 覆盖全页且串行 await 各模块数据（market-wind 4.6s），
// 用户要盯着转圈近 5 秒才看到页面结构。现在结构秒出、KPI 区各自补位。
const loading = ref(false)
const modules = ref<RegistryItem[]>([])
const cardKpis = ref<Record<string, CardKpi[]>>({})
const kpiLoading = ref<Record<string, boolean>>({})

onMounted(async () => {
  loading.value = true
  try {
    const resp: any = await api.get('/analysis/registry')
    modules.value = resp.items ?? []
    // 跟踪型卡片各自拉模块数据取 KPI 摘要（模块多了可改为专用 summary 接口）
    const tracks = modules.value.filter((x) => x.kind === 'track')
    tracks.forEach((m) => {
      kpiLoading.value[m.module_id] = true
    })
    // 并行发起、互不阻塞：耗时 = 最慢的一个，而非各模块之和。
    // silent：单卡取数失败只降级为「不显示 KPI」，不弹全局错误提示。
    void Promise.allSettled(
      tracks.map(async (m) => {
        try {
          const r: any = await api.get(`/analysis/${m.module_id}`, { silent: true })
          cardKpis.value[m.module_id] = (r.kpis ?? []).slice(0, 3)
        } catch {
          cardKpis.value[m.module_id] = []
        } finally {
          kpiLoading.value[m.module_id] = false
        }
      }),
    )
  } catch (e) {
    console.error('[analysis-registry]', e)
  } finally {
    loading.value = false
  }
})

const trackModules = computed(() => modules.value.filter((m) => m.kind === 'track'))
const researchByGroup = computed(() => {
  const map: Record<string, RegistryItem[]> = {}
  for (const m of modules.value.filter((x) => x.kind === 'research')) {
    ;(map[m.group] ??= []).push(m)
  }
  return map
})

function open(m: RegistryItem) {
  router.push(`/analysis/${m.module_id}`)
}
// 与 KpiCards 一致：tone='neutral' 的指标（如大势位置分位）不是涨跌语义，用主色，禁止按符号染红绿
function kpiCls(k: CardKpi): string {
  if (k.value == null) return 'color:#909399'
  if (k.tone === 'neutral') return 'color:#185FA5'
  return k.value > 0 ? 'color:#EF232A' : k.value < 0 ? 'color:#14B143' : 'color:#909399'
}
function kpiText(k: CardKpi): string {
  if (k.value == null) return '--'
  const sign = k.tone !== 'neutral' && k.value > 0 ? '+' : ''
  return `${sign}${k.value}${k.unit ?? ''}`
}
</script>

<template>
  <NSpin :show="loading">
    <h2 class="ao-title">分析研究</h2>

    <!-- 跟踪卡片区 -->
    <div class="ao-section">📊 跟踪</div>
    <div v-if="trackModules.length" class="ao-cards">
      <NCard v-for="m in trackModules" :key="m.module_id" size="small" hoverable class="ao-card" @click="open(m)">
        <div class="ao-card-head">
          <span class="ao-card-name">{{ m.icon }} {{ m.name }}</span>
          <NTag size="tiny" :bordered="false" type="info">跟踪</NTag>
        </div>
        <div class="ao-card-desc"><RichText :text="m.desc" /></div>
        <!-- KPI 区独立占位：骨架高度对齐 .ao-kpi（8+10+行高×3+8），避免数据到达时布局跳动 -->
        <div class="ao-card-kpis" v-if="kpiLoading[m.module_id] || cardKpis[m.module_id]?.length">
          <template v-if="kpiLoading[m.module_id]">
            <NSkeleton v-for="i in 3" :key="i" height="68px" :sharp="false" />
          </template>
          <template v-else>
            <div v-for="k in cardKpis[m.module_id]" :key="k.label" class="ao-kpi">
              <div class="ao-kpi-label" :title="k.label">{{ k.label }}</div>
              <div class="ao-kpi-value" :style="kpiCls(k)">{{ kpiText(k) }}</div>
              <div class="ao-kpi-status">{{ k.status }}</div>
            </div>
          </template>
        </div>
      </NCard>
    </div>
    <NEmpty v-else description="暂无跟踪型模块" size="small" />

    <!-- 研究目录区 -->
    <div class="ao-section">🔬 研究</div>
    <template v-for="g in GROUP_ORDER" :key="g">
      <div v-if="researchByGroup[g]?.length" class="ao-group">
        <div class="ao-group-name">{{ g }}</div>
        <div class="ao-group-items">
          <NCard v-for="m in researchByGroup[g]" :key="m.module_id" size="small" hoverable class="ao-item" @click="open(m)">
            <b>{{ m.icon }} {{ m.name }}</b>
            <div class="ao-item-desc"><RichText :text="m.desc" /></div>
          </NCard>
        </div>
      </div>
    </template>
    <NEmpty v-if="!modules.some((m) => m.kind === 'research')" description="研究型模块陆续接入中…" size="small" />
  </NSpin>
</template>

<style scoped>
.ao-title { margin: 0 0 16px; color: #1F2937; }
.ao-section { font-size: 14px; font-weight: 600; color: #185FA5; margin: 18px 0 10px; }
/* min(440px,100%)：窗口极窄时轨道不小于容器，避免卡片自身撑破内容区 */
.ao-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(440px, 100%), 1fr)); gap: 12px; }
.ao-card { cursor: pointer; }
.ao-card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.ao-card-name { font-size: 15px; font-weight: 600; color: #1F2937; }
.ao-card-desc { font-size: 12px; color: #6B7280; margin: 4px 0 10px; }
/* auto-fit + 确定最小轨宽：KPI 等宽、自适应个数，且数学上不可能撑破卡片
   （曾因 flex:1 的 min-width:auto 溢出 36px，第三个盒子右侧压到页面底） */
.ao-card-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; }
.ao-kpi { min-width: 0; background: #F5F7FA; border-radius: 6px; padding: 8px 10px; }
.ao-kpi-label { font-size: 11px; color: #6B7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ao-kpi-value { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ao-kpi-status { font-size: 11px; color: #6B7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ao-group { margin-bottom: 12px; }
.ao-group-name { font-size: 12px; color: #6B7280; margin-bottom: 6px; }
.ao-group-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.ao-item { cursor: pointer; }
.ao-item-desc { font-size: 12px; color: #6B7280; margin-top: 4px; }
</style>
