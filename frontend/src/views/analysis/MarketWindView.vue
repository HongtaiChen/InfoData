<script setup lang="ts">
/**
 * MarketWindView —— 市场风向模块（分析研究首个跟踪型模块）
 * 数据：GET /api/analysis/market-wind
 * 布局（AnalysisShell 五段）：标题区(容器) / 参数区 / KPI 结论区 / 主视图 / 明细下钻区
 *
 * 2026-09-14 增强：
 * - 市场宽度：补「多少只股票在涨」这一维（原指标全是指数间收益差）
 * - 量能：全市场成交额与 20 日均量比，配合宽度区分「放量下跌 / 缩量止跌」
 * - 标准化：六组收益与各 KPI 附「近一年分位 + z-score」，让绝对 pp 有可比基准
 * - 热力矩阵：六组 × 时间，读趋势持续性（单时点横条看不出谁在持续走强）
 * - 区间底色带：把剪刀差正负渲染成「小盘/大盘占优」区间
 * - 历史回放：as_of 锚点，复盘任意历史交易日的全貌
 * - 下钻：点六组热力条 → 明细表过滤到该组（结论 → 论据）
 */
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NDatePicker, NSelect, NSpin, NTabPane, NTabs, type DataTableColumns,
} from 'naive-ui'
import KpiCards from '../../components/analysis/KpiCards.vue'
import GroupHeatBars from '../../components/analysis/GroupHeatBars.vue'
import SizeGradient from '../../components/analysis/SizeGradient.vue'
import DualLineTrend, { type TrendBand } from '../../components/analysis/DualLineTrend.vue'
import RankTable from '../../components/analysis/RankTable.vue'
import DrillLink from '../../components/analysis/DrillLink.vue'
import BreadthPanel, { type Breadth } from '../../components/analysis/BreadthPanel.vue'
import HeatMatrix, { type MatrixRow } from '../../components/analysis/HeatMatrix.vue'
import CrossCheckPanel, { type CrossItem } from '../../components/analysis/CrossCheckPanel.vue'
import api from '../../api'

const router = useRouter()

interface Kpi {
  key: string; label: string; value: number | null; unit?: string; status?: string; hint?: string
  tone?: 'updown' | 'diff' | 'neutral'   // diff = 组间收益差（主色蓝+带符号），见 registry 卡片墙契约 ④
  card_rank?: number | null
  scale?: { pct: number; label: string } | null   // 分位刻度条（窗口口径由后端下发）
  pct?: number | null   // 近 250 日分位（0~100）
  z?: number | null     // 近 250 日 z-score
  highlight?: boolean   // 分位进极值区 → 金色标记
  anchor?: string       // 点 KPI 卡滚动到的页内锚点
  adj?: number | null   // 风险调整值（收益差 ÷ 自身近250日滚动σ，σ 倍数）；仅风偏与剪刀差有
}
interface GroupRow {
  group: string; ret_20: number | null; ret_60: number | null
  ret_20_pct?: number | null; ret_20_z?: number | null
}
interface GradRow { code: string; name: string; desc: string; ret_20: number | null; change_pct: number | null }
interface VolumeRow {
  amount: number | null; ratio_20: number | null; status: string; pct: number | null; hint?: string
}
interface DetailRow {
  index_code: string; index_name: string; index_group: string; group_desc: string
  close: number | null; change_pct: number | null; ret_20: number | null
}

const loading = ref(false)
const asOf = ref('')
const staleSessions = ref(0)   // as_of 之后已走过的交易日数（0=最新）
const isReplay = ref(false)
const kpis = ref<Kpi[]>([])
// 风险调整口径说明：后端随响应下发，前端只做透传展示（避免前后端各抄一份口径）
const adjNote = ref('')
const groups = ref<GroupRow[]>([])
const gradient = ref<GradRow[]>([])
const trend = ref<{
  dates: string[]; scissors: (number | null)[]; risk_appetite: (number | null)[]
  bands: TrendBand[]
}>({ dates: [], scissors: [], risk_appetite: [], bands: [] })
const detail = ref<DetailRow[]>([])
// 市场宽度（2026-09-14 新增）：指数由权重股主导，只有宽度能回答「上涨是否普遍」
const breadth = ref<Breadth | null>(null)
const volume = ref<VolumeRow | null>(null)
const breadthTrend = ref<{
  dates: string[]; up_ratio: (number | null)[]; adl: (number | null)[]
  above_ma20_pct: (number | null)[]; hl_diff60: (number | null)[]
  amount: (number | null)[]; amount_ratio: (number | null)[]
}>({ dates: [], up_ratio: [], adl: [], above_ma20_pct: [], hl_diff60: [], amount: [], amount_ratio: [] })
const heatMatrix = ref<{ cols: string[]; rows: MatrixRow[] }>({ cols: [], rows: [] })
// 交叉印证（2026-09-15 P1）：五类参照系第 ④ 类 —— 拿股票市场内的维度去跟外部独立维度比，
// 专门找「背离」（两个本该同向的维度不同向）。此前这一类完全空白。
const crossChecks = ref<CrossItem[]>([])
const crossNote = ref('')

const trendDays = ref(250)
const trendDaysOptions = [
  { label: '近半年', value: 120 },
  { label: '近一年', value: 250 },
  { label: '近两年', value: 500 },
]
// 历史回放：null = 最新
const replayTs = ref<number | null>(null)

/** 下钻：明细表按组过滤（点热力条 → 只看该组指数） */
const detailFilter = ref<string>('全部')

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = { trend_days: trendDays.value }
    if (replayTs.value) {
      const d = new Date(replayTs.value)
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      params.as_of = iso
    }
    const resp: any = await api.get('/analysis/market-wind', { params })
    asOf.value = resp.as_of ?? ''
    staleSessions.value = resp.stale_sessions ?? 0
    isReplay.value = !!resp.is_replay
    adjNote.value = resp.adj_note ?? ''
    // tone 由后端 KPI payload 给出（分位数类指标 = neutral，不按红涨绿跌染色）
    kpis.value = (resp.kpis ?? []).map((k: Kpi) => ({ ...k, tone: k.tone ?? 'updown' }))
    groups.value = resp.groups ?? []
    gradient.value = resp.size_gradient ?? []
    trend.value = resp.trend ?? { dates: [], scissors: [], risk_appetite: [], bands: [] }
    detail.value = resp.detail ?? []
    breadth.value = resp.breadth ?? null
    volume.value = resp.volume ?? null
    breadthTrend.value = resp.breadth_trend ?? {
      dates: [], up_ratio: [], adl: [], above_ma20_pct: [], hl_diff60: [], amount: [], amount_ratio: [],
    }
    heatMatrix.value = resp.heat_matrix ?? { cols: [], rows: [] }
    crossChecks.value = resp.cross_checks ?? []
    crossNote.value = resp.cross_note ?? ''
  } catch (e) {
    console.error('[market-wind]', e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

function onReplayChange() {
  detailFilter.value = '全部'
  load()
}
/** 点热力条某组 → 明细表切到该组并滚到明细区 */
function onGroupSelect(label: string) {
  detailFilter.value = label
  document.getElementById('mw-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
/** 点热力矩阵某行 → 同上 */
function onMatrixSelect(label: string) {
  onGroupSelect(label)
}

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

const detailFiltered = computed(() =>
  detailFilter.value === '全部' ? detail.value : detail.value.filter((d) => d.index_group === detailFilter.value)
)
const detailTabs = computed(() => ['全部', ...new Set(detail.value.map((d) => d.index_group))])
</script>

<template>
  <NSpin :show="loading">
    <!-- ② 参数区 -->
    <div class="mw-params">
      <span class="mw-asof">
        数据截至 <b :class="{ stale: staleSessions > 0 }">{{ asOf || '--' }}</b>
        <span v-if="isReplay" class="mw-replay-tag">历史回放</span>
        <span v-else-if="staleSessions > 0" class="mw-stale">· 已落后 {{ staleSessions }} 个交易日</span>
      </span>
      <div class="mw-params-right">
        <NDatePicker
          v-model:value="replayTs"
          type="date"
          size="small"
          clearable
          placeholder="回放某交易日"
          style="width: 148px"
          @update:value="onReplayChange"
        />
        <NSelect v-model:value="trendDays" :options="trendDaysOptions" size="tiny" style="width: 100px" @update:value="load" />
      </div>
    </div>

    <!-- ③ 结论区 -->
    <KpiCards :items="kpis" :adj-note="adjNote" />

    <!-- ④ 主视图 -->
    <NCard id="mw-heat" size="small" class="mw-card" title="六组等权收益（20 日；副标为 60 日与近一年分位，点行下钻该组）">
      <GroupHeatBars
        clickable
        @select="onGroupSelect"
        :rows="groups.map((g) => ({
          label: g.group,
          value: g.ret_20,
          sub: g.ret_60 == null ? '' : `60日 ${g.ret_60 > 0 ? '+' : ''}${g.ret_60.toFixed(2)}%`,
          pctLabel: g.ret_20_pct == null ? '' : `近一年 ${g.ret_20_pct}% 分位`,
          desc: g.ret_20_z == null ? g.group : `${g.group} · z=${g.ret_20_z}`,
        }))"
      />
    </NCard>

    <!-- 市场宽度：补「多少只股票在涨」这一维（原指标全是指数间收益差） -->
    <NCard id="mw-breadth" size="small" class="mw-card" title="市场宽度与量能（个股涨跌家数 / 均线参与度 / 新高新低 / 成交额）">
      <BreadthPanel :data="breadth" />
      <div v-if="volume" class="mw-volume">
        <span class="mw-volume-k">全市场成交额</span>
        <b class="mw-volume-v">{{ volume.amount == null ? '--' : volume.amount.toLocaleString() }} 亿元</b>
        <span class="mw-volume-k">/ 20日均量</span>
        <b class="mw-volume-r">{{ volume.ratio_20 == null ? '--' : volume.ratio_20.toFixed(1) + '%' }}</b>
        <span class="mw-volume-s">{{ volume.status }}</span>
        <span v-if="volume.pct != null" class="mw-volume-p">近一年 {{ volume.pct }}% 分位</span>
      </div>
      <div v-if="breadthTrend.dates.length" class="mw-breadth-trend">
        <!-- 取色纪律：涨跌语义（上涨家数占比）用红；结构性占比用主色蓝；量能用蓝色 ramp 深蓝。
             金色只留给「≤10% 的亮点强调」，不做整条折线色，故此处不用 #C9A227。 -->
        <DualLineTrend
          :dates="breadthTrend.dates"
          :series="[
            { name: '上涨家数占比(%)', values: breadthTrend.up_ratio, color: '#EF232A' },
            { name: '站上MA20占比(%)', values: breadthTrend.above_ma20_pct, color: '#185FA5' },
            { name: '成交额/20日均量(%)', values: breadthTrend.amount_ratio, color: '#0C447C' },
          ]"
          height="230px"
        />
      </div>
    </NCard>

    <!-- 交叉印证（2026-09-15 P1 六项 → 2026-09-19 加「估值印证」为七项）：参照系第 ④ 类。
         七项里每一项都是「股票市场内的一个维度 × 一个独立外部维度」，用途只有一个——发现背离。 -->
    <NCard id="mw-cross" size="small" class="mw-card" title="交叉印证（拿股票市场内的维度，去跟外部独立维度比 —— 专门找「背离」）">
      <CrossCheckPanel :items="crossChecks" :note="crossNote" />
    </NCard>

    <div class="mw-two-col">
      <NCard id="mw-gradient" size="small" class="mw-card" title="市值风格五档（20 日收益）">
        <SizeGradient
          :items="gradient.map((g) => ({ name: g.name, desc: g.desc, value: g.ret_20, change_pct: g.change_pct }))"
        />
      </NCard>
      <NCard id="mw-trend" size="small" class="mw-card" title="风格轮动时序（剪刀差 & 风偏分数；底色带 = 大小盘占优区间）">
        <DualLineTrend
          :dates="trend.dates"
          :bands="trend.bands"
          :series="[
            { name: '大小盘剪刀差(20日)', values: trend.scissors, color: '#185FA5' },
            { name: '风偏分数(20日)', values: trend.risk_appetite, color: '#C9A227' },
          ]"
          height="240px"
        />
      </NCard>
    </div>

    <!-- 六组 × 时间 热力矩阵：看「哪一组在持续走强」，单时点横条做不到 -->
    <NCard id="mw-matrix" size="small" class="mw-card" title="六组风格热力矩阵（时间 × 分组；点行下钻该组）">
      <HeatMatrix :cols="heatMatrix.cols" :rows="heatMatrix.rows" @select="onMatrixSelect" />
    </NCard>

    <!-- ⑤ 明细下钻区 -->
    <NCard id="mw-detail" size="small" class="mw-card" :title="`指数明细（${detailFiltered.length} 只${detailFilter === '全部' ? '' : ' · ' + detailFilter}）`">
      <template #header-extra>
        <DrillLink :items="[{ label: '行情看板看K线', to: '/market' }]" />
      </template>
      <NTabs v-model:value="detailFilter" type="segment" size="small">
        <NTabPane v-for="grp in detailTabs" :key="grp" :name="grp" :tab="grp" />
      </NTabs>
      <div class="mw-detail-table">
        <RankTable :columns="detailColumns" :rows="detailFiltered" :max-height="'360px'" />
      </div>
    </NCard>
  </NSpin>
</template>

<style scoped>
.mw-params { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.mw-asof { font-size: 13px; color: #6B7280; }
/* 滞后提示（设计规范 §2.2）：数据落后于最新交易日时标琥珀，避免静默展示旧数据 */
.mw-asof b.stale, .mw-asof .mw-stale { color: #B45309; }
.mw-replay-tag {
  margin-left: 6px; font-size: 11px; color: #185FA5;
  background: #E6F1FB; border-radius: 3px; padding: 1px 5px;
}
.mw-params-right { display: flex; gap: 8px; align-items: center; }
/* scroll-margin-top：KPI 卡点击滚到锚点时留出顶栏高度，避免卡片标题被顶栏遮住 */
.mw-card { margin-bottom: 12px; scroll-margin-top: 12px; }
.mw-breadth-trend { margin-top: 14px; padding-top: 12px; border-top: 1px dashed #EDEFF2; }
.mw-volume {
  display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
  margin-top: 12px; padding-top: 10px; border-top: 1px dashed #EDEFF2; font-size: 13px;
}
.mw-volume-k { color: #6B7280; }
.mw-volume-v { color: #185FA5; font-size: 15px; font-variant-numeric: tabular-nums; }
.mw-volume-r { color: #1F2937; font-variant-numeric: tabular-nums; }
.mw-volume-s { color: #185FA5; background: #E6F1FB; border-radius: 3px; padding: 0 5px; font-size: 12px; }
.mw-volume-p { color: #9CA3AF; font-size: 12px; }
.mw-two-col { display: grid; grid-template-columns: 1fr 1.4fr; gap: 12px; }
.mw-detail-table { margin-top: 8px; }
@media (max-width: 900px) { .mw-two-col { grid-template-columns: 1fr; } }
</style>
