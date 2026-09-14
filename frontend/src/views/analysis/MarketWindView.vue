<script setup lang="ts">
/**
 * MarketWindView —— 市场风向模块（分析研究首个跟踪型模块）
 * 数据：GET /api/analysis/market-wind
 * 布局（AnalysisShell 五段）：标题区(容器) / 参数区 / KPI 结论区 / 主视图(热力条+梯度条+轮动时序) / 明细下钻区
 */
import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NSelect, NSpin, NTabPane, NTabs, type DataTableColumns } from 'naive-ui'
import KpiCards from '../../components/analysis/KpiCards.vue'
import GroupHeatBars from '../../components/analysis/GroupHeatBars.vue'
import SizeGradient from '../../components/analysis/SizeGradient.vue'
import DualLineTrend from '../../components/analysis/DualLineTrend.vue'
import RankTable from '../../components/analysis/RankTable.vue'
import DrillLink from '../../components/analysis/DrillLink.vue'
import api from '../../api'

const router = useRouter()

interface Kpi { key: string; label: string; value: number | null; unit?: string; status?: string; hint?: string; tone?: 'updown' | 'neutral' }
interface GroupRow { group: string; ret_20: number | null; ret_60: number | null }
interface GradRow { code: string; name: string; desc: string; ret_20: number | null; change_pct: number | null }
interface DetailRow {
  index_code: string; index_name: string; index_group: string; group_desc: string
  close: number | null; change_pct: number | null; ret_20: number | null
}

const loading = ref(false)
const asOf = ref('')
const kpis = ref<Kpi[]>([])
const groups = ref<GroupRow[]>([])
const gradient = ref<GradRow[]>([])
const trend = ref<{ dates: string[]; scissors: (number | null)[]; risk_appetite: (number | null)[] }>({
  dates: [], scissors: [], risk_appetite: [],
})
const detail = ref<DetailRow[]>([])

const trendDays = ref(250)
const trendDaysOptions = [
  { label: '近半年', value: 120 },
  { label: '近一年', value: 250 },
  { label: '近两年', value: 500 },
]

async function load() {
  loading.value = true
  try {
    const resp: any = await api.get('/analysis/market-wind', { params: { trend_days: trendDays.value } })
    asOf.value = resp.as_of ?? ''
    // tone 由后端 KPI payload 给出（分位数类指标 = neutral，不按红涨绿跌染色）
    kpis.value = (resp.kpis ?? []).map((k: Kpi) => ({ ...k, tone: k.tone ?? 'updown' }))
    groups.value = resp.groups ?? []
    gradient.value = resp.size_gradient ?? []
    trend.value = resp.trend ?? { dates: [], scissors: [], risk_appetite: [] }
    detail.value = resp.detail ?? []
  } catch (e) {
    console.error('[market-wind]', e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

// ---- 明细表 ----
const pctRender = (v: any) =>
  v == null
    ? h('span', { style: 'color:#909399' }, '--')
    : h('span', { style: `color:${Number(v) > 0 ? '#EF232A' : Number(v) < 0 ? '#14B143' : '#909399'}` },
        `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`)

const detailColumns: DataTableColumns<DetailRow> = [
  { title: '分组', key: 'index_group', width: 90 },
  { title: '角色', key: 'group_desc', width: 130, ellipsis: { tooltip: true } },
  { title: '代码', key: 'index_code', width: 90 },
  { title: '名称', key: 'index_name', width: 110 },
  { title: '收盘', key: 'close', width: 100, render: (r) => (r.close == null ? '--' : r.close.toFixed(2)) },
  { title: '当日涨跌', key: 'change_pct', width: 100, render: (r) => pctRender(r.change_pct) },
  { title: '20日收益', key: 'ret_20', width: 100, render: (r) => pctRender(r.ret_20) },
  {
    title: '操作', key: 'op', width: 90,
    render: (r) => h(NButton, {
      size: 'tiny', secondary: true,
      onClick: () => router.push({ path: '/market', query: { index: r.index_code, name: r.index_name } }),
    }, { default: () => 'K线' }),
  },
]
</script>

<template>
  <NSpin :show="loading">
    <!-- ② 参数区 -->
    <div class="mw-params">
      <span class="mw-asof">数据截至 <b>{{ asOf || '--' }}</b></span>
      <NSelect v-model:value="trendDays" :options="trendDaysOptions" size="tiny" style="width: 100px" @update:value="load" />
    </div>

    <!-- ③ 结论区 -->
    <KpiCards :items="kpis" />

    <!-- ④ 主视图 -->
    <NCard size="small" class="mw-card" title="六组等权收益（20 日，红涨绿跌）">
      <GroupHeatBars
        :rows="groups.map((g) => ({ label: g.group, value: g.ret_20, sub: g.ret_60 == null ? '' : `60日 ${g.ret_60 > 0 ? '+' : ''}${g.ret_60.toFixed(2)}%` }))"
      />
    </NCard>

    <div class="mw-two-col">
      <NCard size="small" class="mw-card" title="市值风格五档（20 日收益）">
        <SizeGradient
          :items="gradient.map((g) => ({ name: g.name, desc: g.desc, value: g.ret_20, change_pct: g.change_pct }))"
        />
      </NCard>
      <NCard size="small" class="mw-card" title="风格轮动时序（剪刀差 & 风偏分数）">
        <DualLineTrend
          :dates="trend.dates"
          :series="[
            { name: '大小盘剪刀差(20日)', values: trend.scissors, color: '#185FA5' },
            { name: '风偏分数(20日)', values: trend.risk_appetite, color: '#C9A227' },
          ]"
          height="240px"
        />
      </NCard>
    </div>

    <!-- ⑤ 明细下钻区 -->
    <NCard size="small" class="mw-card" title="指数明细（21 只）">
      <template #header-extra>
        <DrillLink :items="[{ label: '行情看板看K线', to: '/market' }]" />
      </template>
      <NTabs type="segment" size="small" default-value="全部">
        <NTabPane name="全部" tab="全部">
          <RankTable :columns="detailColumns" :rows="detail" :max-height="'360px'" />
        </NTabPane>
        <NTabPane v-for="grp in [...new Set(detail.map((d) => d.index_group))]" :key="grp" :name="grp" :tab="grp">
          <RankTable :columns="detailColumns" :rows="detail.filter((d) => d.index_group === grp)" :max-height="'360px'" />
        </NTabPane>
      </NTabs>
    </NCard>
  </NSpin>
</template>

<style scoped>
.mw-params { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.mw-asof { font-size: 13px; color: #6B7280; }
.mw-card { margin-bottom: 12px; }
.mw-two-col { display: grid; grid-template-columns: 1fr 1.4fr; gap: 12px; }
@media (max-width: 900px) { .mw-two-col { grid-template-columns: 1fr; } }
</style>
