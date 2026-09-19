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
  key?: string
  label: string
  value: number | null
  unit?: string
  status?: string
  tone?: 'updown' | 'neutral'
  /** 后端声明「是否上卡片」（1..3，越小越靠前）。总览页据此挑，不按数组顺序截断 */
  card_rank?: number | null
}
/**
 * 卡片顶部的一句话结论。**由后端生成**——口径随响应下发，前端只透传不手抄；
 * 前端拼结论句会立刻产生第二份口径，日后必然漂移（见 backend/app/analysis/registry.py 卡片墙契约）
 */
interface CardVerdict {
  headline: string
  detail?: string
  tone?: 'normal' | 'opportunity' | 'caution'
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
const cardVerdicts = ref<Record<string, CardVerdict>>({})
const kpiLoading = ref<Record<string, boolean>>({})

/**
 * 卡片只放 3 个 KPI：**由后端 card_rank 指定**，不再取数组前 3 个。
 * 原先的 slice(0, 3) 按声明顺序截断——市场风向恰好把带分位的「大势位置」「股债性价比 ERP」
 * 截掉、只留三个同类的 20 日动量（实测 2026-09-19）。口径属于后端，前端只透传。
 * 未标 card_rank 的模块回退为前 3 个，保证既有模块不受影响。
 */
function pickCardKpis(kpis: CardKpi[]): CardKpi[] {
  const ranked = kpis
    .filter((k) => k.card_rank != null)
    .sort((a, b) => Number(a.card_rank) - Number(b.card_rank))
  return (ranked.length ? ranked : kpis).slice(0, 3)
}

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
          cardKpis.value[m.module_id] = pickCardKpis(r.kpis ?? [])
          // 判读条与 KPI 同源同请求，一起到达；没有 verdict 的模块只是不显示判读条
          if (r.verdict?.headline) cardVerdicts.value[m.module_id] = r.verdict
          else delete cardVerdicts.value[m.module_id]
        } catch {
          cardKpis.value[m.module_id] = []
          delete cardVerdicts.value[m.module_id]
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
        <div class="ao-card-desc" :title="m.desc"><RichText :text="m.desc" /></div>

        <!-- 判读条（2026-09-19）：卡片最前的一句话结论。**文案由后端生成**——
             口径随响应下发、前端只透传不手抄（见 backend/app/analysis/registry.py 卡片墙契约）。
             ⚠️ 骨架必须单独占位：它与 KPI 是同一个请求返回，若只给 KPI 占位，
                判读条插入时会把下面整排盒子顶下去，产生可见的布局跳动。 -->
        <div v-if="kpiLoading[m.module_id]" class="ao-verdict-skel">
          <NSkeleton height="54px" :sharp="false" />
        </div>
        <div
          v-else-if="cardVerdicts[m.module_id]"
          class="ao-verdict"
          :class="`ao-verdict--${cardVerdicts[m.module_id].tone || 'normal'}`"
        >
          <div class="ao-verdict-headline">{{ cardVerdicts[m.module_id].headline }}</div>
          <div v-if="cardVerdicts[m.module_id].detail" class="ao-verdict-detail">
            {{ cardVerdicts[m.module_id].detail }}
          </div>
        </div>

        <!-- KPI 区独立占位：骨架高度对齐 .ao-kpi（8+10+行高×3+8），避免数据到达时布局跳动 -->
        <div class="ao-card-kpis" v-if="kpiLoading[m.module_id] || cardKpis[m.module_id]?.length">
          <template v-if="kpiLoading[m.module_id]">
            <NSkeleton v-for="i in 3" :key="i" height="68px" :sharp="false" />
          </template>
          <template v-else>
            <div v-for="k in cardKpis[m.module_id]" :key="k.key || k.label" class="ao-kpi">
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
.ao-card-desc { font-size: 12px; color: #6B7280; margin: 4px 0 8px; }
/* 判读条：卡片的一句话结论。三态配色沿用「蓝骨金魂」——
   常态 = 主色蓝 / 低位机会 = 金（专用于亮点，≤10% 强调）/ 高位提醒 = 琥珀。
   这里**刻意不出现红绿**：红涨绿跌是行情数字与 K 线专用，管理 UI 不参与
   （失败才用深红棕、警告用琥珀）。 */
.ao-verdict { border-left: 3px solid #185FA5; background: #E6F1FB;
  border-radius: 0 6px 6px 0; padding: 8px 10px; margin-bottom: 10px; }
.ao-verdict-headline { font-size: 13px; font-weight: 600; color: #0C447C; line-height: 1.45; }
.ao-verdict-detail { font-size: 11px; color: #185FA5; margin-top: 4px; line-height: 1.5; }
.ao-verdict--opportunity { border-left-color: #C9A227; background: #FAF3DF; }
.ao-verdict--opportunity .ao-verdict-headline { color: #7A5E12; }
.ao-verdict--opportunity .ao-verdict-detail { color: #A4841F; }
.ao-verdict--caution { border-left-color: #B45309; background: #FAEEDA; }
.ao-verdict--caution .ao-verdict-headline { color: #633806; }
.ao-verdict--caution .ao-verdict-detail { color: #854F0B; }
.ao-verdict-skel { margin-bottom: 10px; }
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
