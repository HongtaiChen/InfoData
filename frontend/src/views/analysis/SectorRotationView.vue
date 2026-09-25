<script setup lang="ts">
/**
 * SectorRotationView —— 板块轮动模块（分析研究·板块与概念）
 * 数据：GET /api/analysis/sector-rotation
 *
 * 落的是设计规范 §1.0 的参照系 ① **同类横比** 与 ③ **基准超额**：
 * - 行业之间谁强谁弱（①）—— 单报「银行 +5.23%」没有意义，得跟其他 30 个行业比
 * - 行业相对市场基准是真强还是水涨船高（③）—— 普跌日里 −2% 的行业其实是「跑赢」
 * 末尾再做一次 ④ **交叉印证**：申万行业口径 vs 同花顺概念口径是否互证。
 *
 * 行业离散度 = 各行业 20 日收益的标准差，是「轮动速度」的量化描述 ——
 * 单看排行看不出这件事（齐涨齐跌与剧烈轮动会给出相似的排行观感）。
 *
 * ⚠️ 行业排行需扫个股日线快照（约 8 秒），后端已套 10 分钟 TTL 缓存（见 backend/app/analysis/_cache.py）。
 */
import { computed, h, onMounted, ref } from 'vue'
import {
  NCard, NDatePicker, NSelect, NSpin, NTabPane, NTabs, type DataTableColumns,
} from 'naive-ui'
import KpiCards from '../../components/analysis/KpiCards.vue'
import GroupHeatBars from '../../components/analysis/GroupHeatBars.vue'
import RankTable from '../../components/analysis/RankTable.vue'
import DrillLink from '../../components/analysis/DrillLink.vue'
import RichText from '../../components/analysis/RichText.vue'
import api from '../../api'

interface Kpi {
  key: string; label: string; value: number | null; unit?: string
  status?: string; hint?: string
  tone?: 'updown' | 'diff' | 'neutral'   // diff = 收益中位等相对强弱，主色蓝+带符号（registry 契约 ④）
  card_rank?: number | null
  scale?: { pct: number; label: string } | null   // 本模块无历史行业收益序列 → 不下发
  pct?: number | null; highlight?: boolean; anchor?: string; adj?: number | null
}
interface IndRow { name: string; n: number; ret_20: number | null; excess: number | null }
interface ConceptRow { name: string; ret_20: number | null }

const loading = ref(false)
const asOf = ref('')
const baseDate = ref('')
const isReplay = ref(false)
const kpis = ref<Kpi[]>([])
const note = ref('')
const industry = ref<{
  level: string; items: IndRow[]; count: number; stock_count: number
  median: number | null; up_ratio: number | null; dispersion: number | null
  spread: number | null; bench_ret_20: number | null; base_date: string
} | null>(null)
const concept = ref<{
  as_of: string | null; items: ConceptRow[]; count: number
  median: number | null; up_ratio: number | null; outliers: number
} | null>(null)
const compare = ref<{ level: string; verdict: string; reading: string } | null>(null)

const level = ref('一级')
const levelOptions = [
  { label: '申万一级（31 个）', value: '一级' },
  { label: '申万二级（131 个）', value: '二级' },
]
const replayTs = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = { level: level.value }
    if (replayTs.value) {
      const d = new Date(replayTs.value)
      params.as_of = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    }
    const resp: any = await api.get('/analysis/sector-rotation', { params })
    asOf.value = resp.as_of ?? ''
    baseDate.value = resp.base_date ?? ''
    isReplay.value = !!resp.is_replay
    note.value = resp.note ?? ''
    kpis.value = (resp.kpis ?? []).map((k: Kpi) => ({ ...k, tone: k.tone ?? 'updown' }))
    industry.value = resp.industry ?? null
    concept.value = resp.concept ?? null
    compare.value = resp.compare ?? null
  } catch (e) {
    console.error('[sector-rotation]', e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

/** 行业横条：主值=20日收益，副标=相对市场基准超额（③基准超额） */
const industryRows = computed(() =>
  (industry.value?.items ?? []).map((i) => ({
    label: i.name,
    value: i.ret_20,
    sub: i.excess == null ? '' : `超额 ${i.excess > 0 ? '+' : ''}${i.excess.toFixed(2)}pp`,
    desc: `${i.name} · ${i.n} 只个股等权 · 20日 ${i.ret_20}% · 相对基准 ${i.excess}pp`,
  }))
)

const conceptTop = computed(() => (concept.value?.items ?? []).slice(0, 12))
const conceptBottom = computed(() => (concept.value?.items ?? []).slice(-12).reverse())

const compareCardClass = computed(() =>
  compare.value?.level === 'diverge' ? 'sr-compare--diverge' : 'sr-compare--agree'
)

const conceptTabs = ref('领涨')
const conceptVisible = computed(() =>
  conceptTabs.value === '领涨' ? conceptTop.value : conceptBottom.value
)
function toRows(list: ConceptRow[]) {
  return list.map((c) => ({
    label: c.name, value: c.ret_20,
    desc: `${c.name} · 20日 ${c.ret_20}%`,
  }))
}

const pctRender = (v: any) =>
  v == null
    ? h('span', { style: 'color:#909399' }, '--')
    : h('span', { style: `color:${Number(v) > 0 ? '#EF232A' : Number(v) < 0 ? '#14B143' : '#909399'}` },
        `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`)

const indColumns: DataTableColumns<IndRow> = [
  {
    title: '排名', key: 'rank', width: 60,
    render: (_r, idx) => String(idx + 1),
  },
  { title: '行业', key: 'name', width: 120 },
  { title: '成分数', key: 'n', width: 80 },
  { title: '20日收益', key: 'ret_20', width: 100, render: (r) => pctRender(r.ret_20) },
  { title: '相对基准超额', key: 'excess', width: 120, render: (r) => pctRender(r.excess) },
]

const conColumns: DataTableColumns<ConceptRow> = [
  { title: '排名', key: 'rank', width: 60, render: (_r, idx) => String(idx + 1) },
  { title: '概念', key: 'name', width: 200, ellipsis: { tooltip: true } },
  { title: '20日收益', key: 'ret_20', width: 110, render: (r) => pctRender(r.ret_20) },
]
</script>

<template>
  <NSpin :show="loading">
    <!-- ② 参数区 -->
    <div class="sr-params">
      <span class="sr-asof">
        数据截至 <b>{{ asOf || '--' }}</b>
        <span class="sr-base">（基准日 {{ baseDate || '--' }}，20 个交易日）</span>
        <span v-if="isReplay" class="sr-replay-tag">历史回放</span>
      </span>
      <div class="sr-params-right">
        <NDatePicker
          v-model:value="replayTs" type="date" size="small" clearable
          placeholder="回放某交易日" style="width: 148px" @update:value="load"
        />
        <NSelect v-model:value="level" :options="levelOptions" size="tiny" style="width: 150px" @update:value="load" />
      </div>
    </div>

    <!-- ③ 结论区 -->
    <KpiCards :items="kpis" />

    <!-- ④ 主视图：行业排行（同等权 + 相对基准超额）
         标题按规范 §1.3 契约③只写「回答什么问题」——原先写作
         「申万一级行业 20 日收益排行（31 个行业 · 成分股 5216 只等权）」，把计数与口径塞进标题。
         计数随数据漂移，口径属卡内 caption。 -->
    <NCard id="sr-industry" size="small" class="sr-card"
           :title="`申万${industry?.level ?? '一级'}行业收益排行：谁在领涨`">
      <template #header-extra>
        <DrillLink :items="[{ label: '行情看板看大盘', to: '/market' }]" />
      </template>
      <div class="sr-sum">
        <span>行业中位 <b class="sr-num">{{ industry?.median == null ? '--' : industry.median + '%' }}</b></span>
        <span>上涨行业占比 <b class="sr-num">{{ industry?.up_ratio ?? '--' }}%</b></span>
        <span>相对市场基准 <b class="sr-num">{{ industry?.bench_ret_20 == null ? '--' : industry.bench_ret_20 + '%' }}</b></span>
        <span class="sr-hintline">同类横比 + 基准超额：普跌日里跑赢基准的行业才是真强</span>
      </div>
      <GroupHeatBars :rows="industryRows" />
      <div class="card-cap">
        口径：{{ industry?.count ?? 0 }} 个行业 · 成分股 {{ industry?.stock_count ?? 0 }} 只等权
      </div>
    </NCard>

    <!-- 概念口径：与行业口径正交的第二套数据集 -->
    <NCard id="sr-concept" size="small" class="sr-card" title="概念收益排行：谁在领涨、谁在掉队">
      <template #header-extra>
        <DrillLink :items="[{ label: '概念中心看K线', to: '/concept' }]" />
      </template>
      <div class="sr-sum">
        <span>概念中位 <b class="sr-num">{{ concept?.median == null ? '--' : concept.median + '%' }}</b></span>
        <span>上涨概念占比 <b class="sr-num">{{ concept?.up_ratio ?? '--' }}%</b></span>
        <span>概念数据截至 <b class="sr-num">{{ concept?.as_of || '--' }}</b></span>
      </div>
      <NTabs v-model:value="conceptTabs" type="segment" size="small" style="max-width: 220px; margin-bottom: 10px">
        <NTabPane name="领涨" tab="领涨 12" />
        <NTabPane name="领跌" tab="领跌 12" />
      </NTabs>
      <GroupHeatBars :rows="toRows(conceptVisible)" />
      <div class="card-cap">
        口径：{{ concept?.count ?? 0 }} 个概念{{ concept?.outliers ? ` · 已剔除 ${concept.outliers} 个异常样本（|20 日收益| &gt; 60%）` : '' }}
      </div>
    </NCard>

    <!-- ④ 交叉印证：两个独立数据集是否互证
         原标题「双侧口径互证（申万行业 vs 同花顺概念 —— 两个独立数据集是否给出同一结论）」
         括号里是一整句解释，读者要先读完才知道这张卡干什么。 -->
    <NCard id="sr-compare" size="small" class="sr-card" title="两个口径是否互证？">
      <div class="sr-compare" :class="compareCardClass">
        <span class="sr-compare-badge">{{ compare?.verdict ?? '--' }}</span>
        <span class="sr-compare-text"><RichText :text="compare?.reading" /></span>
      </div>
      <div class="sr-note"><RichText :text="note" /></div>
      <div class="card-cap">互证对象：申万行业口径 ↔ 同花顺概念口径（两个独立数据集）</div>
    </NCard>

    <!-- ⑤ 明细区（标题不再带计数 —— 计数随数据漂移，下沉为卡内 caption） -->
    <div class="sr-two-col">
      <NCard id="sr-ind-detail" size="small" class="sr-card" title="行业明细">
        <RankTable :columns="indColumns" :rows="industry?.items ?? []" :max-height="'380px'" />
        <div class="card-cap">共 {{ industry?.count ?? 0 }} 个行业</div>
      </NCard>
      <NCard id="sr-con-detail" size="small" class="sr-card" title="概念明细">
        <RankTable :columns="conColumns" :rows="concept?.items ?? []" :max-height="'380px'" />
        <div class="card-cap">共 {{ concept?.count ?? 0 }} 个概念</div>
      </NCard>
    </div>
  </NSpin>
</template>

<style scoped>
.sr-params { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.sr-asof { font-size: 13px; color: #6B7280; }
.sr-asof b { color: #1F2937; }
.sr-base { color: #9CA3AF; }
.sr-replay-tag {
  margin-left: 6px; font-size: 11px; color: #185FA5;
  background: #E6F1FB; border-radius: 3px; padding: 1px 5px;
}
.sr-params-right { display: flex; gap: 8px; align-items: center; }
.sr-card { margin-bottom: 12px; scroll-margin-top: 12px; }
.sr-sum {
  display: flex; gap: 18px; align-items: baseline; flex-wrap: wrap;
  font-size: 12px; color: #6B7280; margin-bottom: 12px;
  padding-bottom: 10px; border-bottom: 1px dashed #EDEFF2;
}
.sr-num { color: #185FA5; font-size: 14px; font-variant-numeric: tabular-nums; }
.sr-hintline { color: #9CA3AF; font-size: 11px; }

/* 口径分歧是最有价值的信号 → 琥珀；一致用主色蓝（与 CrossCheckPanel 同一套纪律，不用红绿） */
.sr-compare { display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: 6px; border: 1px solid #EDEFF2; border-left: 3px solid #D4D7DE; }
.sr-compare--diverge { border-left-color: #B45309; background: #FFFBF3; }
.sr-compare--agree { border-left-color: #185FA5; }
.sr-compare-badge { font-size: 11px; border-radius: 3px; padding: 1px 6px; flex: none; margin-top: 1px; }
.sr-compare--diverge .sr-compare-badge { background: #FEF3C7; color: #B45309; font-weight: 600; }
.sr-compare--agree .sr-compare-badge { background: #E6F1FB; color: #185FA5; }
.sr-compare-text { font-size: 12px; color: #374151; line-height: 1.65; }
.sr-note { font-size: 11px; color: #9CA3AF; margin-top: 8px; line-height: 1.6; }
.sr-two-col { display: grid; grid-template-columns: 1fr 1.2fr; gap: 12px; }
@media (max-width: 1100px) { .sr-two-col { grid-template-columns: 1fr; } }
</style>
