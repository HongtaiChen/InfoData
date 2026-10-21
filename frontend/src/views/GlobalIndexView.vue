<script setup lang="ts">
/**
 * GlobalIndexView —— 投资环境·国际指数（v1.1，2026-10-21）
 * 参照国内指数风格，分两大块 + 左侧两级锚点导航（与国内指数页同款）：
 *   A. 技术面指标：① 指数总览与深钻 ② 趋势与动量 ③ 波动率与风险 ④ 资金流与仓位 ⑤ 跨市场联动
 *   B. 基本面分析：① 估值水平 ② 盈利增长与质量 ③ 宏观经济与政策 ④ 国际政治与地缘风险
 * v1.1 性能优化：分块渐进加载（总览先出、各块独立 loading，互不阻塞），
 *   fx-bond/CFTC 后端裁剪近 10 年，避免 20+ ECharts 全量初始化阻塞首屏。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NCard, NSpin, NEmpty } from 'naive-ui'
import api from '../api'
import IndicatorBlock from '../components/analysis/IndicatorBlock.vue'
import HistoryCurve from '../components/analysis/HistoryCurve.vue'
import CorrelationMatrix from '../components/analysis/CorrelationMatrix.vue'

// ---------- 状态（分块独立 loading，互不阻塞） ----------
const groups = ref<any[]>([])
const tech = ref<any>(null)
const vix = ref<any>(null)
const cftc = ref<any>(null)
const fxBond = ref<any>(null)
const corr = ref<any>(null)
const fundamental = ref<any>(null)
const overviewLoading = ref(true)
const vixLoading = ref(true)
const cftcLoading = ref(true)
const fxBondLoading = ref(true)
const corrLoading = ref(true)
const fundLoading = ref(true)
const techLoading = ref(false)
const currentCode = ref('SPX')
const currentName = ref('标普500')

// ---------- 左侧锚点导航（与国内指数页同款） ----------
const NAV_ITEMS = [
  {
    id: 'sec-overview', label: '技术面分析',
    children: [
      { id: 'sec-overview', label: '指数总览与深钻' },
      { id: 'sec-trend', label: '趋势与动量' },
      { id: 'sec-risk', label: '波动率与风险' },
      { id: 'sec-flow', label: '资金流与仓位' },
      { id: 'sec-cross', label: '跨市场联动' },
    ],
  },
  {
    id: 'sec-fund', label: '基本面分析',
    children: [
      { id: 'sec-valuation', label: '估值水平' },
      { id: 'sec-earnings', label: '盈利增长与质量' },
      { id: 'sec-macro', label: '宏观经济与政策' },
      { id: 'sec-geo', label: '国际政治与地缘风险' },
    ],
  },
]
const activeNav = ref<string>('sec-overview')

function scrollTo(id: string) {
  activeNav.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// 滚动时高亮当前区块（取视口顶部 120px 线以上最近的一个锚点）
const FLAT_NAV: { id: string; parent: string }[] = NAV_ITEMS.flatMap(
  (g) => g.children.map((c) => ({ id: c.id, parent: g.id })),
)
function onDocScroll() {
  const line = 120
  let cur = FLAT_NAV[0].id
  for (const n of FLAT_NAV) {
    const el = document.getElementById(n.id)
    if (el && el.getBoundingClientRect().top <= line) cur = n.id
  }
  activeNav.value = cur
  // 导航吸顶：本页滚动发生在嵌套 layout 的外层滚动容器，CSS sticky 相对内层计算会失效
  // （国内指数页同款处理：JS translateY 模拟 sticky，钳制在 aside 列内）
  updateNavPin()
}

// ---------- 导航吸顶（JS 模拟 sticky，与国内指数页同款自校正方案） ----------
const navInnerRef = ref<HTMLElement | null>(null)
const navAsideRef = ref<HTMLElement | null>(null)

function updateNavPin() {
  const aside = navAsideRef.value
  const inner = navInnerRef.value
  if (!aside || !inner) return
  const maxShift = Math.max(0, aside.offsetHeight - inner.offsetHeight)
  const shift = Math.min(Math.max(8 - aside.getBoundingClientRect().top, 0), maxShift)
  inner.style.transform = `translateY(${shift}px)`
}
onMounted(() => {
  document.addEventListener('scroll', onDocScroll, { capture: true, passive: true })
  window.addEventListener('resize', updateNavPin, { passive: true })
  updateNavPin()
})
onBeforeUnmount(() => {
  document.removeEventListener('scroll', onDocScroll, { capture: true } as any)
  window.removeEventListener('resize', updateNavPin)
})

// ---------- 工具 ----------
function fmtNum(v: any, digits = 2): string {
  if (v == null) return '--'
  return Number(v).toFixed(digits)
}
function fmtPct(v: any, digits = 2): string {
  if (v == null) return '--'
  return `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`
}
function pctClass(v: number | null): string {
  if (v == null) return ''
  return v > 0 ? 'c-up' : v < 0 ? 'c-down' : ''
}

// ---------- 数据加载（分块渐进：总览先出，其余并行但各自独立落位） ----------
async function loadOverview() {
  try {
    const g: any = await api.get('/environment/global/indices')
    groups.value = g.groups || []
    const allItems = groups.value.flatMap((gr: any) => gr.items)
    const cur = allItems.find((it: any) => it.index_code === currentCode.value) || allItems[0]
    if (cur) {
      currentCode.value = cur.index_code
      currentName.value = cur.index_name
    }
  } catch (e) {
    console.error('[global indices]', e)
  } finally {
    overviewLoading.value = false
  }
}

async function loadTech() {
  techLoading.value = true
  try {
    tech.value = await api.get(`/environment/global/indices/${currentCode.value}/tech`) as any
  } catch (e) {
    console.error('[global tech]', e)
    tech.value = null
  } finally {
    techLoading.value = false
  }
}

async function loadVix() {
  try { vix.value = await api.get('/environment/global/vix') as any }
  catch (e) { console.error('[global vix]', e) }
  finally { vixLoading.value = false }
}
async function loadCftc() {
  try { cftc.value = await api.get('/environment/global/cftc') as any }
  catch (e) { console.error('[global cftc]', e) }
  finally { cftcLoading.value = false }
}
async function loadFxBond() {
  try { fxBond.value = await api.get('/environment/global/fx-bond') as any }
  catch (e) { console.error('[global fx-bond]', e) }
  finally { fxBondLoading.value = false }
}
async function loadCorr() {
  try { corr.value = await api.get('/environment/global/correlation') as any }
  catch (e) { console.error('[global corr]', e) }
  finally { corrLoading.value = false }
}
async function loadFund() {
  try { fundamental.value = await api.get('/environment/global/fundamental') as any }
  catch (e) { console.error('[global fund]', e) }
  finally { fundLoading.value = false }
}

async function setCurrent(code: string, name: string) {
  currentCode.value = code
  currentName.value = name
  await loadTech()
}

onMounted(() => {
  // 总览先出（首屏最快可见），技术面深钻紧跟；其余块各自独立加载、渲染好一块亮一块
  loadOverview().then(loadTech)
  loadVix()
  loadCftc()
  loadFxBond()
  loadCorr()
  loadFund()
})

// ---------- CFTC 序列（转成 HistoryCurve 格式） ----------
const cftcSeries = computed(() => {
  if (!cftc.value || cftc.value.empty) return { dates: [], series: [] as any[] }
  const dates = cftc.value.dates || []
  const pick = ['USD', 'EUR', 'JPY', 'GBP'].filter((c) => (cftc.value.currencies || []).includes(c))
  const palette = ['#185FA5', '#C9A227', '#EF232A', '#14B143']
  const series = pick.map((c, i) => ({
    name: c,
    values: cftc.value.series?.[c] || [],
    color: palette[i % palette.length],
  }))
  return { dates, series }
})

// ---------- 估值分组（按指标，数据来自 fundamental.valuation） ----------
const valuationPe = computed(() => {
  const items = fundamental.value?.valuation?.items || []
  if (!items.length) return []
  return items.map((it: any) => ({
    index_code: it.index_code,
    index_name: it.index_name,
    pe_ttm: it.metrics?.pe_ttm?.latest,
    pe_fwd: it.metrics?.pe_fwd?.latest,
    cape: it.metrics?.cape?.latest,
  }))
})

// ---------- 基本面（4 大块） ----------
const spxDividend = computed(() => {
  const f = fundamental.value
  if (!f || f.valuation?.empty) return null
  const spx = (f.valuation.items || []).find((i: any) => i.index_code === 'SPX')
  const dy = spx?.metrics?.dividend_yield
  if (!dy || !dy.values?.length) return null
  return { dates: dy.dates || [], values: dy.values || [], latest: dy.latest, latest_date: dy.latest_date }
})

const macroIndicators = computed(() => {
  const f = fundamental.value
  if (!f || f.macro?.empty) return []
  const defs = [
    { key: 'gdp_growth', label: '实际 GDP 增速', unit: '%' },
    { key: 'inflation', label: 'CPI 通胀（年均）', unit: '%' },
    { key: 'debt_gdp', label: '政府债务 / GDP', unit: '%' },
  ]
  const countries = f.macro.countries || []
  return defs.map((d) => {
    const series: any[] = []
    const yearSet = new Set<number>()
    countries.forEach((c: any) => {
      const ind = c.indicators?.[d.key]
      if (ind?.years) ind.years.forEach((y: number) => yearSet.add(y))
    })
    const sortedYears = Array.from(yearSet).sort((a, b) => a - b)
    countries.forEach((c: any, i: number) => {
      const ind = c.indicators?.[d.key]
      if (!ind) return
      const valMap = new Map<number, number>()
      ind.years.forEach((y: number, j: number) => valMap.set(y, ind.values[j]))
      series.push({
        name: c.country_code,
        values: sortedYears.map((y) => valMap.get(y) ?? null),
        color: ['#185FA5', '#C9A227', '#EF232A', '#14B143', '#6B7280', '#7C4DB3', '#00838F', '#D96B0A', '#5B8FF9', '#E6647C', '#13C2C2', '#2FC25B'][i % 12],
      })
    })
    return { key: d.key, label: d.label, unit: d.unit, years: sortedYears.map(String), series }
  })
})

const hicp = computed(() => {
  const f = fundamental.value
  if (!f || f.macro?.hicp?.empty) return null
  return f.macro.hicp
})

const policyRates = computed(() => {
  const f = fundamental.value
  if (!f || f.macro?.policy_rate?.empty) return []
  return f.macro.policy_rate.countries || []
})
</script>

<template>
  <div class="env-layout">
    <!-- 左侧页内锚点导航（与国内指数页同款两级结构） -->
    <aside ref="navAsideRef" class="env-nav">
      <div ref="navInnerRef" class="env-nav-inner">
        <div v-for="g in NAV_ITEMS" :key="g.id" class="env-nav-group">
          <div
            class="env-nav-item env-nav-parent"
            :class="{ on: g.children.some((c) => c.id === activeNav) }"
            @click="scrollTo(g.children[0].id)"
          >{{ g.label }}</div>
          <div
            v-for="c in g.children"
            :key="c.id"
            class="env-nav-item env-nav-child"
            :class="{ on: activeNav === c.id }"
            @click="scrollTo(c.id)"
          >{{ c.label }}</div>
        </div>
      </div>
    </aside>

    <div class="env">
      <!-- 顶部说明 -->
      <NCard size="small" class="env-scope">
        <p class="scope-p"><b>样本指数</b>：18 个国际指数，按 6 组组织（全球综合 / 美国 / 欧洲 / 日本 / 新兴市场 / 中国相关）。数据来自 MSCI 官方公开 API（免 Key）+ 新浪 + akshare。</p>
        <p class="scope-p"><b>口径</b>：技术面从各指数本币收盘价物化算出；⚠️ 各市场本币计价，汇率可能扭曲跨市场趋势信号。结论由观看者判断，只呈现客观数值。</p>
      </NCard>

      <!-- ============ A. 技术面指标 ============ -->
      <div class="env-section" id="sec-overview">
        <h2 class="env-h2">技术面指标</h2>

        <!-- 指数分组总览（点击深钻） -->
        <NCard size="small" class="ov-card">
          <NSpin :show="overviewLoading" size="small">
            <div class="group-grid">
              <div v-for="gr in groups" :key="gr.key" class="group-block">
                <div class="group-label">{{ gr.label }}</div>
                <div
                  v-for="it in gr.items"
                  :key="it.index_code"
                  class="group-item"
                  :class="{ cur: it.index_code === currentCode }"
                  @click="setCurrent(it.index_code, it.index_name)"
                >
                  <span class="gi-name">{{ it.index_name }}</span>
                  <span class="gi-pct" :class="pctClass(it.change_pct)">{{ fmtPct(it.change_pct) }}</span>
                </div>
              </div>
            </div>
          </NSpin>
          <div class="ov-note">📖 点击指数名深钻下方技术面指标。红 = 涨、绿 = 跌（当日涨跌幅，各市场本币口径）。</div>
        </NCard>

        <!-- 当前指数标题 -->
        <div class="cur-head">
          <span class="cur-title">{{ currentName }}</span>
          <span v-if="tech && !tech.empty" class="cur-sub">
            {{ tech.as_of }} · 收盘 {{ fmtNum(tech.close) }} ·
            <span :class="pctClass(tech.change_pct)">{{ fmtPct(tech.change_pct) }}</span>
          </span>
        </div>
      </div>

      <!-- ① 趋势与动量（依赖深钻 tech，独立 loading） -->
      <div class="env-section" id="sec-trend">
        <template v-if="techLoading">
          <NCard size="small"><NSpin size="small" /></NCard>
        </template>
        <template v-else-if="tech?.empty">
          <NEmpty :description="tech?.note || '暂无技术面数据'" />
        </template>
        <template v-else-if="tech">
          <h3 class="env-h3">① 趋势与动量</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="均线位置（60日 / 250日）"
              :value="`MA60 ${fmtPct(tech.trend?.moving_avg?.close_over_ma60_pct)} · MA250 ${fmtPct(tech.trend?.moving_avg?.close_over_ma250_pct)}`"
              :note="tech.trend?.moving_avg?.note"
            >
              <HistoryCurve
                :dates="tech.trend?.moving_avg?.dates || []"
                :series="[
                  { name: '收盘', values: tech.trend?.moving_avg?.close || [], color: '#185FA5' },
                  { name: 'MA60', values: tech.trend?.moving_avg?.ma60 || [], color: '#C9A227', dashed: true },
                  { name: 'MA250', values: tech.trend?.moving_avg?.ma250 || [], color: '#909399', dashed: true },
                ]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="MACD / RSI"
              :value="`RSI ${fmtNum(tech.trend?.rsi?.value, 1)}`"
              :note="tech.trend?.macd?.note"
            >
              <HistoryCurve
                :dates="tech.trend?.rsi?.dates || []"
                :series="[
                  { name: 'RSI14', values: tech.trend?.rsi?.rsi14 || [], color: '#185FA5' },
                ]"
                :ref-lines="[{ value: 70, label: '70 超买' }, { value: 30, label: '30 超卖' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="相对强弱（跨市场）"
              :value="`近60日 ${fmtPct(tech.trend?.relative_strength?.value_60)} · 近250日 ${fmtPct(tech.trend?.relative_strength?.value_250)}`"
              :note="tech.trend?.relative_strength?.note"
            >
              <HistoryCurve
                :dates="tech.trend?.relative_strength?.dates || []"
                :series="[
                  { name: '近60日超额', values: tech.trend?.relative_strength?.rel_strong_60 || [], color: '#185FA5' },
                  { name: '近250日超额', values: tech.trend?.relative_strength?.rel_strong_250 || [], color: '#C9A227', dashed: true },
                ]"
                :ref-lines="[{ value: 0, label: '0 跑平全球' }]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>
        </template>
      </div>

      <!-- ② 波动率与风险 -->
      <div class="env-section" id="sec-risk">
        <template v-if="tech && !tech.empty && !techLoading">
          <h3 class="env-h3">② 波动率与风险</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="指数波动率"
              :value="`年化 ${fmtNum(tech.risk?.volatility?.ann_vol_60, 1)}% · 下行 ${fmtNum(tech.risk?.volatility?.down_vol_60, 1)}%`"
              :note="tech.risk?.volatility?.note"
            >
              <HistoryCurve
                :dates="tech.risk?.volatility?.dates || []"
                :series="[
                  { name: '年化波动率', values: tech.risk?.volatility?.ann_vol || [], color: '#185FA5' },
                  { name: '下行波动率', values: tech.risk?.volatility?.down_vol || [], color: '#EF232A' },
                ]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="最大回撤"
              :value="`近1年 ${fmtPct(tech.risk?.drawdown?.max_dd_1y)} · 近3年 ${fmtPct(tech.risk?.drawdown?.max_dd_3y)} · 全史 ${fmtPct(tech.risk?.drawdown?.max_dd_all)}`"
              :note="tech.risk?.drawdown?.note"
            >
              <HistoryCurve
                :dates="tech.risk?.drawdown?.dates || []"
                :series="[
                  { name: '近1年', values: tech.risk?.drawdown?.dd_1y || [], color: '#185FA5' },
                  { name: '近3年', values: tech.risk?.drawdown?.dd_3y || [], color: '#C9A227' },
                  { name: '全史', values: tech.risk?.drawdown?.dd_all || [], color: '#909399' },
                ]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="VIX 恐慌指数（全球）"
              :value="vix && !vix.empty ? fmtNum(vix.latest, 1) : '--'"
              :note="vix?.note"
            >
              <HistoryCurve
                v-if="!vixLoading"
                :dates="vix?.dates || []"
                :series="[{ name: 'VIX', values: vix?.close || [], color: '#185FA5' }]"
                :ref-lines="[{ value: 15, label: '15 平稳' }, { value: 30, label: '30 恐慌' }]"
                height="240px"
                :zoom="true"
              />
              <NSpin v-else size="small" />
            </IndicatorBlock>
          </div>
        </template>
      </div>

      <!-- ③ 资金流与仓位 -->
      <div class="env-section" id="sec-flow">
        <template v-if="tech && !tech.empty && !techLoading">
          <h3 class="env-h3">③ 资金流与仓位</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="期货持仓（CFTC 非商业净仓位）"
              :value="'周频 · ' + (cftc?.latest_date || '--')"
              :note="cftc?.note"
            >
              <template v-if="cftcLoading">
                <NSpin size="small" />
              </template>
              <template v-else-if="cftcSeries.series.length">
                <HistoryCurve
                  :dates="cftcSeries.dates"
                  :series="cftcSeries.series"
                  height="260px"
                  :zoom="true"
                />
              </template>
              <NEmpty v-else description="CFTC 数据未采集" />
            </IndicatorBlock>

            <IndicatorBlock
              title="全球股票基金资金流（EPFR）"
              value="数据源待接入"
              note="EPFR 全球资金流为付费数据，无免费替代源。本期留空，待接入付费 API 后补充。"
            >
              <NEmpty description="EPFR 付费数据源，暂未接入" />
            </IndicatorBlock>

            <IndicatorBlock
              title="资金流集中度"
              value="数据源待接入"
              note="资金流集中度依赖 EPFR 持仓明细（付费），无免费替代源。本期留空。"
            >
              <NEmpty description="依赖 EPFR 数据，暂未接入" />
            </IndicatorBlock>
          </div>
        </template>
      </div>

      <!-- ④ 跨市场联动与风险传染 -->
      <div class="env-section" id="sec-cross">
        <template v-if="tech && !tech.empty && !techLoading">
          <h3 class="env-h3">④ 跨市场联动与风险传染</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="美元指数 DXY"
              :value="fxBond?.dxy?.latest != null ? fmtNum(fxBond.dxy.latest, 2) : '--'"
              :note="fxBond?.dxy?.note"
            >
              <HistoryCurve
                v-if="!fxBondLoading"
                :dates="fxBond?.dxy?.dates || []"
                :series="[{ name: 'DXY', values: fxBond?.dxy?.close || [], color: '#185FA5' }]"
                height="240px"
                :zoom="true"
              />
              <NSpin v-else size="small" />
            </IndicatorBlock>

            <IndicatorBlock
              title="美债 10Y 与利差"
              :value="`10Y ${fmtNum(fxBond?.bond?.latest_y10, 2)}% · 10Y-2Y ${fmtNum(fxBond?.bond?.latest_spread, 2)}%`"
              :note="fxBond?.bond?.note"
            >
              <HistoryCurve
                v-if="!fxBondLoading"
                :dates="fxBond?.bond?.dates || []"
                :series="[
                  { name: '10Y', values: fxBond?.bond?.y10 || [], color: '#185FA5' },
                  { name: '10Y-2Y', values: fxBond?.bond?.spread_10y2y || [], color: '#C9A227', yAxisIndex: 1 },
                ]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
              <NSpin v-else size="small" />
            </IndicatorBlock>

            <IndicatorBlock
              title="当前指数对 ACWI 的 Beta / 相关性"
              :value="`Beta ${fmtNum(tech.risk?.beta_corr?.beta_60, 2)} · 相关 ${fmtNum(tech.risk?.beta_corr?.corr_60, 2)}`"
              :note="tech.risk?.beta_corr?.note"
            >
              <HistoryCurve
                :dates="tech.risk?.beta_corr?.dates || []"
                :series="[
                  { name: 'Beta', values: tech.risk?.beta_corr?.beta || [], color: '#185FA5' },
                  { name: '相关性', values: tech.risk?.beta_corr?.corr || [], color: '#C9A227', yAxisIndex: 1 },
                ]"
                :ref-lines="[{ value: 1, label: 'Beta=1' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <!-- 相关性矩阵 -->
          <h3 class="env-h3">跨市场相关性矩阵（60 日）</h3>
          <NCard size="small">
            <CorrelationMatrix v-if="!corrLoading" :labels="corr?.labels || []" :items="corr?.items || []" />
            <NSpin v-else size="small" />
            <div class="ov-note">📖 相关系数：正相关蓝、负相关绿。相关性急剧上升 = 分散化失效、风险传染加剧。</div>
          </NCard>
        </template>
      </div>

      <!-- ============ B. 基本面分析 ============ -->
      <div class="env-section" id="sec-fund">
        <h2 class="env-h2">基本面分析</h2>
      </div>

      <!-- ① 估值水平 -->
      <div class="env-section" id="sec-valuation">
        <h3 class="env-h3">① 估值水平</h3>
        <template v-if="fundLoading">
          <NCard size="small"><NSpin size="small" /></NCard>
        </template>
        <template v-else-if="fundamental?.valuation?.empty">
          <NEmpty :description="fundamental?.valuation?.note || '暂无估值数据'" />
        </template>
        <template v-else>
          <NCard size="small" class="ov-card">
            <div class="val-table">
              <div class="val-row val-head">
                <span class="val-name">指数</span>
                <span class="val-cell">PE-TTM</span>
                <span class="val-cell">PE-Fwd</span>
                <span class="val-cell">CAPE</span>
                <span class="val-cell">股息率</span>
              </div>
              <div v-for="it in valuationPe" :key="it.index_code" class="val-row">
                <span class="val-name">{{ it.index_name }}</span>
                <span class="val-cell">{{ fmtNum(it.pe_ttm, 2) }}</span>
                <span class="val-cell">{{ fmtNum(it.pe_fwd, 2) }}</span>
                <span class="val-cell">{{ fmtNum(it.cape, 2) }}</span>
                <span class="val-cell">{{ it.index_code === 'SPX' ? fmtNum(spxDividend?.latest, 2) + '%' : '--' }}</span>
              </div>
            </div>
            <div class="ov-note">📖 {{ fundamental?.valuation?.note }}。估值高低由观看者自行对比判断，不配「低估/高估」结论。</div>
          </NCard>

          <!-- 标普500 股息率长历史 -->
          <div class="block-grid">
            <IndicatorBlock
              v-if="spxDividend"
              title="标普500 股息率（Shiller，1871 起）"
              :value="`${fmtNum(spxDividend.latest, 2)}%`"
              :note="'标普500 回溯 12 个月股息 ÷ 月末价格（Shiller 学术源）。最新 ' + spxDividend.latest_date + '；股息率越高现金回报安全垫越厚。'"
            >
              <HistoryCurve
                :dates="spxDividend.dates"
                :series="[{ name: '股息率', values: spxDividend.values, color: '#185FA5' }]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="PB（市净率）"
              value="数据源待接入"
              note="国际指数 PB 免费源不可得：Siblis 免费层 PB 恒为 null，MSCI/STOXX Fact Sheet 需登录或订阅。本期如实留空，不伪造；待接入 Siblis 付费层 / MSCI Fact Sheet 后补充。"
            >
              <NEmpty description="PB 免费源不可得，待接入付费数据源" />
            </IndicatorBlock>

            <IndicatorBlock
              title="股息率（除标普500外）"
              value="数据源待接入"
              note="MSCI/STOXX 指数的股息率免费源不可得（MSCI Fact Sheet 需登录，MacroMicro/iShares 仅网页无稳定 API）。标普500 已由 Shiller 覆盖，其余指数待接入后补充。"
            >
              <NEmpty description="其余指数股息率待接入" />
            </IndicatorBlock>
          </div>
        </template>
      </div>

      <!-- ② 盈利增长与质量 -->
      <div class="env-section" id="sec-earnings">
        <h3 class="env-h3">② 盈利增长与质量</h3>
        <NCard size="small" class="ov-card">
          <div class="gap-box">
            <div class="gap-title">⚠️ 本块数据源缺口</div>
            <p class="gap-p">{{ fundamental?.earnings?.note || '暂无数据' }}</p>
            <div class="gap-list">
              <div v-for="(v, k) in (fundamental?.earnings?.proxies || {})" :key="k" class="gap-item">
                <span class="gap-key">{{ k }}</span>
                <span class="gap-val">{{ v }}</span>
              </div>
            </div>
          </div>
          <div class="ov-note">📖 盈利增长与质量（ROE / EPS 增速 / 盈利修正比率）是估值安全的基础，但指数级免费历史序列全球缺失，需 IBES/FactSet/Bloomberg 商业授权。本期诚实留空，不做伪数据。</div>
        </NCard>
      </div>

      <!-- ③ 宏观经济与政策 -->
      <div class="env-section" id="sec-macro">
        <h3 class="env-h3">③ 宏观经济与政策</h3>
        <template v-if="fundLoading">
          <NCard size="small"><NSpin size="small" /></NCard>
        </template>
        <template v-else-if="!fundamental?.macro || fundamental?.macro?.empty">
          <NEmpty description="宏观数据未采集" />
        </template>
        <template v-else>
          <!-- GDP 增速 / 通胀 / 债务GDP 三组曲线 -->
          <div class="block-grid">
            <IndicatorBlock
              v-for="mi in macroIndicators"
              :key="mi.key"
              :title="mi.label + '（IMF WEO）'"
              :value="''"
              :note="'IMF WEO（World Economic Outlook，权威一手源，半年更新）年度序列，含历史与预测。' + mi.label + ' 按 ' + mi.unit + ' 口径；预测年已混入，观看者需区分实际与预测。'"
            >
              <HistoryCurve
                :dates="mi.years"
                :series="mi.series"
                :y-format="mi.unit"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <!-- 欧元区 HICP 月度（及时）+ 政策利率 -->
          <div class="block-grid">
            <IndicatorBlock
              v-if="hicp"
              title="欧元区 HICP（月度，及时）"
              :value="`${fmtNum(hicp.latest, 2)}%`"
              :note="'欧元区调和消费者物价指数同比（Eurostat，ECB 目标基准口径）。最新 ' + hicp.latest_month + '。这是本域唯一可得的及时月度国际通胀源。'"
            >
              <HistoryCurve
                :dates="hicp.dates"
                :series="[{ name: 'HICP 同比', values: hicp.values, color: '#185FA5' }]"
                :ref-lines="[{ value: 2, label: '2% 目标' }]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="央行政策利率（四大央行）"
              :value="''"
              :note="fundamental?.macro?.policy_rate?.note || '央行政策利率历史决议序列'"
            >
              <HistoryCurve
                v-if="policyRates.length"
                :dates="policyRates[0]?.dates || []"
                :series="policyRates.map((c: any, i: number) => ({
                  name: c.country_name,
                  values: c.rates,
                  color: ['#185FA5', '#C9A227', '#EF232A', '#14B143'][i % 4],
                }))"
                y-format="%"
                height="240px"
                :zoom="true"
              />
              <NEmpty v-else description="政策利率未采集" />
            </IndicatorBlock>

            <IndicatorBlock
              title="全球 PMI（制造业/服务业）"
              value="数据源待接入"
              note="全球 PMI（S&P Global，J.P.Morgan 合作发布）免费仅当月标题值，完整历史序列需付费订阅。OECD BCI 可作弱代理但口径不同。本期留空，待接入付费 PMI 后补充。"
            >
              <NEmpty description="全球 PMI 免费长历史不可得" />
            </IndicatorBlock>
          </div>
        </template>
      </div>

      <!-- ④ 国际政治与地缘风险 -->
      <div class="env-section" id="sec-geo">
        <h3 class="env-h3">④ 国际政治与地缘风险</h3>
        <NCard size="small" class="ov-card">
          <div class="geo-grid">
            <div class="geo-item">
              <div class="geo-title">地缘冲突（中东 / 俄乌）</div>
              <div class="geo-desc">冲突升级 → 能源价格（油价/气价）显著上升，触发波动率（VIX）脉冲、风险偏好回落。属定性判断，无结构化免费数据源。</div>
            </div>
            <div class="geo-item">
              <div class="geo-title">关税与贸易政策</div>
              <div class="geo-desc">贸易政策不确定性上升 → 跨国企业盈利预期承压、供应链重构。属定性判断。</div>
            </div>
            <div class="geo-item">
              <div class="geo-title">制裁与资产冻结</div>
              <div class="geo-desc">可能切断正常流动性渠道、扰动资本流动。属定性判断。</div>
            </div>
            <div class="geo-item">
              <div class="geo-title">主权信用与财政可持续性</div>
              <div class="geo-desc">日债长端收益率大幅上升、美欧走势分化 → 长端利率与估值中枢受扰。可引用库内 10Y 收益率（上方「美债 10Y」块）作数值证据。</div>
            </div>
          </div>
          <div class="ov-note">📖 本块为定性框架，无结构化免费数据源。量化观测已在上方「跨市场联动」（VIX/美元/美债）与「宏观」（债务/GDP）呈现，可交叉印证。</div>
        </NCard>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 页内导航 + 内容两栏布局；与国内指数页同款 */
.env-layout { display: flex; gap: 14px; align-items: flex-start; max-width: 1440px; margin: 0 auto; }
.env-nav {
  flex: none; width: 150px; align-self: stretch;
  /* sticky 放在内层 .env-nav-inner：外层 stretch 撑满整列提供行程 */
}
.env-nav-inner {
  display: flex; flex-direction: column; gap: 2px;
  padding: 8px 6px; background: #FFFFFF; border: 1px solid #EDEFF2; border-radius: 8px;
  will-change: transform;
}
.env-nav-item {
  font-size: 12px; color: #6B7280; padding: 6px 8px; border-radius: 5px; cursor: pointer;
  white-space: normal; word-break: break-all; user-select: none; line-height: 1.45;
}
.env-nav-parent { font-weight: 600; color: #1F2937; }
.env-nav-parent.on { color: #185FA5; }
.env-nav-child { padding-left: 20px; font-size: 12px; }
.env-nav-item:hover { color: #185FA5; background: #F0F6FD; }
.env-nav-item.on { color: #185FA5; background: #E3EDF9; font-weight: 600; }
.env { flex: 1; min-width: 0; max-width: 1280px; }
.env-scope { margin-bottom: 12px; }
.scope-p { font-size: 13px; line-height: 1.7; color: #374151; margin: 6px 0; }
.scope-p b { color: #1F2937; }
.env-section { margin-bottom: 8px; }
.env-h2 {
  font-size: 17px; font-weight: 700; color: #185FA5; margin: 18px 0 12px;
  padding-left: 8px; border-left: 3px solid #185FA5;
}
.env-h3 { font-size: 14px; font-weight: 600; color: #374151; margin: 16px 0 10px; }
.ov-card { margin-bottom: 12px; }
.ov-note { margin-top: 10px; font-size: 12px; line-height: 1.6; color: #6B7280; }
/* 分组总览 */
.group-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.group-block { border: 1px solid #EDEFF2; border-radius: 6px; padding: 10px; }
.group-label { font-size: 12px; font-weight: 600; color: #909399; margin-bottom: 6px; }
.group-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 6px; border-radius: 4px; cursor: pointer; font-size: 13px;
}
.group-item:hover { background: #F0F6FD; }
.group-item.cur { background: #E3EDF9; }
.gi-name { color: #374151; }
.group-item.cur .gi-name { color: #185FA5; font-weight: 600; }
.gi-pct { font-variant-numeric: tabular-nums; font-weight: 600; }
.c-up { color: #EF232A; }
.c-down { color: #14B143; }
/* 当前指数标题 */
.cur-head { display: flex; align-items: baseline; gap: 12px; margin: 8px 0 4px; }
.cur-title { font-size: 16px; font-weight: 700; color: #1F2937; }
.cur-sub { font-size: 12px; color: #6B7280; font-variant-numeric: tabular-nums; }
/* 指标块网格 */
.block-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: start; }
/* 估值表 */
.val-table { width: 100%; border-collapse: collapse; }
.val-row { display: flex; align-items: center; border-bottom: 1px solid #F0F2F5; padding: 8px 6px; }
.val-row.val-head { border-bottom: 2px solid #E5E9EF; font-weight: 600; color: #6B7280; font-size: 12px; }
.val-name { flex: 1; font-size: 13px; color: #374151; }
.val-head .val-name { color: #6B7280; }
.val-cell { flex: 0 0 90px; text-align: right; font-size: 13px; color: #1F2937; font-variant-numeric: tabular-nums; }
.val-head .val-cell { color: #6B7280; }
/* 数据缺口说明框 */
.gap-box { padding: 4px 2px; }
.gap-title { font-size: 14px; font-weight: 600; color: #791F1F; margin-bottom: 8px; }
.gap-p { font-size: 13px; line-height: 1.7; color: #374151; margin: 0 0 12px; }
.gap-list { display: flex; flex-direction: column; gap: 6px; }
.gap-item { display: flex; gap: 10px; font-size: 12.5px; line-height: 1.6; }
.gap-key { flex: 0 0 110px; color: #6B7280; font-weight: 500; }
.gap-val { flex: 1; color: #374151; }
/* 地缘风险网格 */
.geo-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.geo-item { border: 1px solid #EDEFF2; border-radius: 6px; padding: 12px; }
.geo-title { font-size: 13px; font-weight: 600; color: #1F2937; margin-bottom: 6px; }
.geo-desc { font-size: 12.5px; line-height: 1.7; color: #6B7280; }
/* 响应式：窄屏降为单列 */
@media (max-width: 1100px) {
  .block-grid, .group-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .block-grid, .group-grid { grid-template-columns: 1fr; }
}
</style>
