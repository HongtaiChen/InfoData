<script setup lang="ts">
/**
 * MarketMoodView —— 投资环境·市场氛围（v1.0，2026-10-21）
 * 参照国内/国际指数风格，分两大块 + 左侧两级锚点导航：
 *   A. 技术面指标：
 *      ① 交易热度与量能  ② 市场广度与赚钱效应  ③ 情绪与舆论
 *      ④ 资金与杠杆      ⑤ 风险偏好与衍生品
 *   B. 基本面分析：
 *      ① 宏观与政策  ② 外部环境  ③ 供给与制度
 * 数据源：market_style_daily 物化（2005 起）+ 两融/开户/新基金/ETF 既有表；基本面复用
 *   env_macro_monthly + 政策判读卡。连板/封板/北向/社交情绪/股指基差/AH溢价 诚实标注缺口。
 * 结论由观看者判断：只呈现客观数值与分位，不配定性词。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NCard, NSpin, NEmpty } from 'naive-ui'
import api from '../api'
import IndicatorBlock from '../components/analysis/IndicatorBlock.vue'
import HistoryCurve from '../components/analysis/HistoryCurve.vue'

const data = ref<any>(null)
const loading = ref(true)
const policyBusy = ref('')

// ---------- 左侧锚点导航（与国内/国际指数页同款） ----------
const NAV_ITEMS = [
  {
    id: 'sec-heat', label: '技术面指标',
    children: [
      { id: 'sec-heat', label: '交易热度与量能' },
      { id: 'sec-breadth', label: '市场广度与赚钱效应' },
      { id: 'sec-sentiment', label: '情绪与舆论' },
      { id: 'sec-capital', label: '资金与杠杆' },
      { id: 'sec-deriv', label: '风险偏好与衍生品' },
    ],
  },
  {
    id: 'sec-fund', label: '基本面分析',
    children: [
      { id: 'sec-fund-macro', label: '宏观与政策' },
      { id: 'sec-fund-policy', label: '外部环境' },
      { id: 'sec-fund-supply', label: '供给与制度' },
    ],
  },
]
const activeNav = ref<string>('sec-heat')

function scrollTo(id: string) {
  activeNav.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

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
  updateNavPin()
}

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
  load()
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
function fmtYi(v: any): string {
  // 输入为「亿元」：≥1 万亿显示万亿，否则显示亿
  if (v == null || isNaN(Number(v))) return '--'
  const n = Number(v)
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(2)} 万亿`
  return `${n.toFixed(0)} 亿`
}
/** 截至日期 → 「截至 YYYY-MM-DD」；无则空串 */
function asOfLabel(d: any): string {
  return d ? `截至 ${String(d).slice(0, 10)}` : ''
}

// ---------- 数据加载 ----------
async function load() {
  loading.value = true
  try {
    data.value = await api.get('/environment/sentiment') as any
  } catch (e) {
    console.error('[market mood]', e)
  } finally {
    loading.value = false
  }
}

// 政策判读切换（复用国内指数页交互）
const policyItems = computed(() => data.value?.fundamental?.policy?.items ?? [])
async function togglePolicy(topic: string, state: string) {
  policyBusy.value = topic
  try {
    await api.post(`/environment/domestic/policy?topic=${topic}&state=${state}`)
    await load()
  } catch (e) {
    console.error('[policy]', e)
  } finally {
    policyBusy.value = ''
  }
}

// ---------- 派生 ----------
const heat = computed(() => data.value?.heat)
const breadth = computed(() => data.value?.breadth)
const sentimentBlock = computed(() => data.value?.sentiment)
const capital = computed(() => data.value?.capital)
const derivatives = computed(() => data.value?.derivatives)
const fundamental = computed(() => data.value?.fundamental)
</script>

<template>
  <div class="env-layout">
    <!-- 左侧页内锚点导航 -->
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
        <p class="scope-p"><b>口径</b>：全市场口径（A 股沪深两市，约 5000+ 个股）。技术面读库内 market_style_daily 物化数据（2005 年起），两融/开户/新基金/ETF 复用既有表。</p>
        <p class="scope-p"><b>呈现</b>：只呈现客观数值与历史分位，不配「过热/冰点」等定性结论——极端与否由观看者结合当前位置自行判断。红 = 涨、绿 = 跌（仅行情数字）。</p>
      </NCard>

      <NSpin :show="loading">
        <!-- ============ A. 技术面指标 ============ -->
        <div class="env-section" id="sec-heat">
          <h2 class="env-h2">技术面指标</h2>
          <h3 class="env-h3">① 交易热度与量能</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="两市成交额"
              :as-of="asOfLabel(heat?.amount?.latest_date)"
              :value="heat?.amount?.latest != null ? fmtYi(heat.amount.latest) : '--'"
              :note="heat?.amount?.note"
            >
              <HistoryCurve
                :dates="heat?.amount?.dates || []"
                :series="[{ name: '成交额(亿)', values: heat?.amount?.values || [], color: '#185FA5' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="量能比（成交额 / 20 日均量）"
              :as-of="asOfLabel(heat?.amount_ratio?.latest_date)"
              :value="heat?.amount_ratio?.latest != null ? fmtNum(heat.amount_ratio.latest, 0) + '%' : '--'"
              :note="heat?.amount_ratio?.note"
            >
              <HistoryCurve
                :dates="heat?.amount_ratio?.dates || []"
                :series="[{ name: '量能比', values: heat?.amount_ratio?.values || [], color: '#185FA5' }]"
                :ref-lines="[{ value: 100, label: '100 平量' }]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="换手率中位数"
              :as-of="asOfLabel(heat?.turnover?.latest_date)"
              :value="heat?.turnover?.latest != null ? fmtNum(heat.turnover.latest, 2) + '%' : '--'"
              :note="heat?.turnover?.note"
            >
              <HistoryCurve
                :dates="heat?.turnover?.dates || []"
                :series="[{ name: '换手中位数', values: heat?.turnover?.values || [], color: '#185FA5' }]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <!-- 成交集中度 + 轮动速度：引用已有模块 -->
          <div class="block-grid">
            <IndicatorBlock
              title="成交集中度（前 5% 个股成交额占比）"
              value="见分析研究"
              :note="heat?.concentration?.note"
            >
              <NEmpty description="见「分析研究·市场情绪」模块" />
            </IndicatorBlock>
            <IndicatorBlock
              title="板块轮动速度"
              value="见分析研究"
              :note="heat?.rotation?.note"
            >
              <NEmpty description="见「分析研究·板块轮动」模块" />
            </IndicatorBlock>
          </div>
        </div>

        <!-- ② 市场广度与赚钱效应 -->
        <div class="env-section" id="sec-breadth">
          <h3 class="env-h3">② 市场广度与赚钱效应</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="上涨 / 下跌家数"
              :as-of="asOfLabel(breadth?.up_down?.up?.latest_date)"
              :value="`涨 ${breadth?.up_down?.up?.latest ?? '--'} · 跌 ${breadth?.up_down?.down?.latest ?? '--'}`"
              :note="breadth?.up_down?.note"
            >
              <HistoryCurve
                :dates="breadth?.up_down?.up?.dates || []"
                :series="[
                  { name: '上涨', values: breadth?.up_down?.up?.values || [], color: '#EF232A' },
                  { name: '下跌', values: breadth?.up_down?.down?.values || [], color: '#14B143' },
                ]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="上涨家数占比"
              :as-of="asOfLabel(breadth?.up_down?.ratio?.latest_date)"
              :value="breadth?.up_down?.ratio?.latest != null ? fmtNum(breadth.up_down.ratio.latest, 0) + '%' : '--'"
              :note="'普涨还是普跌：上涨家数 ÷ 全市场个股数。'"
            >
              <HistoryCurve
                :dates="breadth?.up_down?.ratio?.dates || []"
                :series="[{ name: '上涨占比', values: breadth?.up_down?.ratio?.values || [], color: '#185FA5' }]"
                :ref-lines="[{ value: 50, label: '50 平衡' }]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="涨停 / 跌停家数"
              :as-of="asOfLabel(breadth?.limit?.up?.latest_date)"
              :value="`涨停 ${breadth?.limit?.up?.latest ?? '--'} · 跌停 ${breadth?.limit?.down?.latest ?? '--'}`"
              :note="breadth?.limit?.note"
            >
              <HistoryCurve
                :dates="breadth?.limit?.up?.dates || []"
                :series="[
                  { name: '涨停', values: breadth?.limit?.up?.values || [], color: '#EF232A' },
                  { name: '跌停', values: breadth?.limit?.down?.values || [], color: '#14B143' },
                ]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock
              title="站上均线个股占比"
              :as-of="asOfLabel(breadth?.above_ma?.ma20?.latest_date)"
              :value="`MA20 ${breadth?.above_ma?.ma20?.latest != null ? fmtNum(breadth.above_ma.ma20.latest, 0) + '%' : '--'} · MA60 ${breadth?.above_ma?.ma60?.latest != null ? fmtNum(breadth.above_ma.ma60.latest, 0) + '%' : '--'}`"
              :note="breadth?.above_ma?.note"
            >
              <HistoryCurve
                :dates="breadth?.above_ma?.ma20?.dates || []"
                :series="[
                  { name: 'MA20 上占比', values: breadth?.above_ma?.ma20?.values || [], color: '#185FA5' },
                  { name: 'MA60 上占比', values: breadth?.above_ma?.ma60?.values || [], color: '#C9A227' },
                ]"
                y-format="%"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="创 60 日新高 / 新低家数"
              :as-of="asOfLabel(breadth?.new_high_low?.high60?.latest_date)"
              :value="`新高 ${breadth?.new_high_low?.high60?.latest ?? '--'} · 新低 ${breadth?.new_high_low?.low60?.latest ?? '--'}`"
              :note="breadth?.new_high_low?.note"
            >
              <HistoryCurve
                :dates="breadth?.new_high_low?.high60?.dates || []"
                :series="[
                  { name: '创新高', values: breadth?.new_high_low?.high60?.values || [], color: '#EF232A' },
                  { name: '创新低', values: breadth?.new_high_low?.low60?.values || [], color: '#14B143' },
                ]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="腾落线 ADL（累计）"
              :as-of="asOfLabel(breadth?.adl?.latest_date)"
              :value="breadth?.adl?.latest != null ? fmtNum(breadth.adl.latest, 0) : '--'"
              :note="breadth?.adl?.note"
            >
              <HistoryCurve
                :dates="breadth?.adl?.dates || []"
                :series="[{ name: 'ADL', values: breadth?.adl?.values || [], color: '#185FA5' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ breadth?.link_note }}</div>
        </div>

        <!-- ③ 情绪与舆论 -->
        <div class="env-section" id="sec-sentiment">
          <h3 class="env-h3">③ 情绪与舆论</h3>
          <NCard size="small" class="ov-card">
            <div class="gap-box">
              <div class="gap-title">⚠️ 本块数据源缺口</div>
              <p class="gap-p">{{ sentimentBlock?.note || '暂无数据' }}</p>
              <div class="gap-list">
                <div v-for="(v, k) in (sentimentBlock?.proxies || {})" :key="k" class="gap-item">
                  <span class="gap-key">{{ k }}</span>
                  <span class="gap-val">{{ v }}</span>
                </div>
              </div>
            </div>
          </NCard>
        </div>

        <!-- ④ 资金与杠杆 -->
        <div class="env-section" id="sec-capital">
          <h3 class="env-h3">④ 资金与杠杆</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="两融余额（融资余额）"
              :as-of="asOfLabel(capital?.margin?.latest_date)"
              :value="capital?.margin?.latest_rzye != null ? fmtYi(capital.margin.latest_rzye) : '--'"
              :note="capital?.margin?.note"
            >
              <HistoryCurve
                :dates="capital?.margin?.dates || []"
                :series="[{ name: '融资余额(亿)', values: capital?.margin?.rzye || [], color: '#185FA5' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="新增 A 股投资者（月频）"
              :as-of="asOfLabel(capital?.accounts?.latest_month)"
              :value="capital?.accounts?.latest != null ? fmtNum(capital.accounts.latest, 0) + ' 万' : '--'"
              :note="capital?.accounts?.note"
            >
              <HistoryCurve
                :dates="capital?.accounts?.dates || []"
                :series="[{ name: '新增开户(万)', values: capital?.accounts?.values || [], color: '#185FA5' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="新基金发行（月频）"
              :as-of="asOfLabel(capital?.fund_issue?.latest_month)"
              :value="capital?.fund_issue?.latest != null ? fmtNum(capital.fund_issue.latest, 0) + ' 只' : '--'"
              :note="capital?.fund_issue?.note"
            >
              <HistoryCurve
                :dates="capital?.fund_issue?.dates || []"
                :series="[{ name: '新发基金(只)', values: capital?.fund_issue?.values || [], color: '#185FA5' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock
              title="股票型 ETF 净申赎"
              :as-of="asOfLabel(capital?.etf_flow?.latest_date)"
              :value="capital?.etf_flow?.latest != null ? fmtNum(capital.etf_flow.latest, 1) + ' 亿' : '--'"
              :note="capital?.etf_flow?.note"
            >
              <HistoryCurve
                v-if="(capital?.etf_flow?.dates || []).length > 1"
                :dates="capital?.etf_flow?.dates || []"
                :series="[{ name: '净申赎(亿)', values: capital?.etf_flow?.net_inflow || [], color: '#185FA5' }]"
                height="240px"
                :zoom="true"
              />
              <NEmpty v-else description="ETF 净申赎数据积累中" />
            </IndicatorBlock>

            <IndicatorBlock
              title="北向 / 南向资金"
              value="数据源待接入"
              :note="capital?.north_south?.note"
            >
              <NEmpty description="北向已停实时披露，待接入沪深港通" />
            </IndicatorBlock>

            <IndicatorBlock
              title="产业资本增减持"
              value="见分析研究"
              :note="capital?.repurchase?.note"
            >
              <NEmpty description="见「分析研究·资金温度」产业资本卡" />
            </IndicatorBlock>
          </div>
        </div>

        <!-- ⑤ 风险偏好与衍生品 -->
        <div class="env-section" id="sec-deriv">
          <h3 class="env-h3">⑤ 风险偏好与衍生品</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="黄金（避险资产）"
              :as-of="asOfLabel(derivatives?.gold?.latest_date)"
              :value="derivatives?.gold?.latest != null ? fmtNum(derivatives.gold.latest, 1) : '--'"
              :note="derivatives?.gold?.note"
            >
              <HistoryCurve
                :dates="derivatives?.gold?.dates || []"
                :series="[{ name: '黄金现货', values: derivatives?.gold?.values || [], color: '#C9A227' }]"
                height="240px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="股指期货基差（IF/IC/IM 升贴水）"
              value="数据源待接入"
              :note="derivatives?.futures_basis?.note"
            >
              <NEmpty description="股指期货品种待接入" />
            </IndicatorBlock>

            <IndicatorBlock
              title="AH 溢价"
              value="数据源待接入"
              :note="derivatives?.ah_premium?.note"
            >
              <NEmpty description="AH 溢价指数待接入" />
            </IndicatorBlock>
          </div>
        </div>

        <!-- ============ B. 基本面分析 ============ -->
        <div class="env-section" id="sec-fund">
          <h2 class="env-h2">基本面分析</h2>
          <h3 class="env-h3" id="sec-fund-macro">① 宏观与政策</h3>
          <div class="block-grid">
            <IndicatorBlock
              v-if="fundamental?.macro?.gdp"
              title="GDP 增速"
              :as-of="asOfLabel(fundamental?.macro?.gdp?.latest)"
              :value="fundamental.macro.gdp.latest_yoy != null ? fmtPct(fundamental.macro.gdp.latest_yoy) : '--'"
              :note="'GDP 同比（国家统计局，季度）。企稳回升 vs 下行压力加大。'"
            >
              <HistoryCurve
                :dates="fundamental.macro.gdp.dates || []"
                :series="[{ name: 'GDP 同比', values: fundamental.macro.gdp.yoy || [], color: '#185FA5' }]"
                y-format="%"
                height="220px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              v-if="fundamental?.macro?.pmi"
              title="PMI（制造业）"
              :as-of="asOfLabel(fundamental?.macro?.pmi?.latest)"
              :value="fundamental.macro.pmi.latest_value != null ? fmtNum(fundamental.macro.pmi.latest_value, 1) : '--'"
              :note="'制造业 PMI（月频）：50 荣枯线。经济景气度先行指标。'"
            >
              <HistoryCurve
                :dates="fundamental.macro.pmi.dates || []"
                :series="[{ name: 'PMI', values: fundamental.macro.pmi.value || [], color: '#185FA5' }]"
                :ref-lines="[{ value: 50, label: '50 荣枯线' }]"
                height="220px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              v-if="fundamental?.macro?.cpi"
              title="CPI（通胀）"
              :as-of="asOfLabel(fundamental?.macro?.cpi?.latest)"
              :value="fundamental.macro.cpi.latest_yoy != null ? fmtPct(fundamental.macro.cpi.latest_yoy) : '--'"
              :note="'CPI 同比（月频）：温和回升 vs 通缩。价格与居民端。'"
            >
              <HistoryCurve
                :dates="fundamental.macro.cpi.dates || []"
                :series="[{ name: 'CPI 同比', values: fundamental.macro.cpi.yoy || [], color: '#185FA5' }]"
                y-format="%"
                height="220px"
                :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              v-if="fundamental?.macro?.ppi"
              title="PPI（工业品价格）"
              :as-of="asOfLabel(fundamental?.macro?.ppi?.latest)"
              :value="fundamental.macro.ppi.latest_yoy != null ? fmtPct(fundamental.macro.ppi.latest_yoy) : '--'"
              :note="'PPI 同比（月频）：价格与企业利润。通缩或成本冲击。'"
            >
              <HistoryCurve
                :dates="fundamental.macro.ppi.dates || []"
                :series="[{ name: 'PPI 同比', values: fundamental.macro.ppi.yoy || [], color: '#185FA5' }]"
                y-format="%"
                height="220px"
                :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ fundamental?.macro?.note }}</div>

          <!-- ② 外部环境 + 政策判读卡 -->
          <h3 class="env-h3" id="sec-fund-policy">② 外部环境</h3>
          <div class="policy-grid">
            <div v-for="t in policyItems" :key="'policy-' + t.topic" class="policy-card">
              <div class="policy-title">{{ t.title }}</div>
              <div class="policy-options">
                <div
                  v-for="o in t.options"
                  :key="o.key"
                  class="policy-opt"
                  :class="{ on: t.state === o.key }"
                  :title="o.hint"
                  @click="togglePolicy(t.topic, o.key)"
                >
                  <span class="policy-dot" />
                  <span class="policy-label">{{ o.label }}</span>
                </div>
              </div>
              <div class="policy-updated" v-if="t.updated_at">判读于 {{ t.updated_at.slice(0, 16) }}</div>
            </div>
          </div>
          <div class="ov-note">📖 政策与外部环境为人工定性判读（两态互斥，点击切换即落库），供复盘时与行情对照；悬停可见各状态的判读含义。</div>

          <!-- ③ 供给与制度 -->
          <h3 class="env-h3" id="sec-fund-supply">③ 供给与制度</h3>
          <NCard size="small" class="ov-card">
            <div class="gap-box">
              <div class="gap-title">⚠️ 本块为定性判断</div>
              <p class="gap-p">{{ fundamental?.supply?.note || '暂无数据' }}</p>
            </div>
          </NCard>
        </div>
      </NSpin>
    </div>
  </div>
</template>

<style scoped>
/* 页内导航 + 内容两栏布局；与国内/国际指数页同款 */
.env-layout { display: flex; gap: 14px; align-items: flex-start; max-width: 1440px; margin: 0 auto; }
.env-nav { flex: none; width: 150px; align-self: stretch; }
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
.block-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: start; }
/* 数据缺口说明框 */
.gap-box { padding: 4px 2px; }
.gap-title { font-size: 14px; font-weight: 600; color: #791F1F; margin-bottom: 8px; }
.gap-p { font-size: 13px; line-height: 1.7; color: #374151; margin: 0 0 12px; }
.gap-list { display: flex; flex-direction: column; gap: 6px; }
.gap-item { display: flex; gap: 10px; font-size: 12.5px; line-height: 1.6; }
.gap-key { flex: 0 0 110px; color: #6B7280; font-weight: 500; }
.gap-val { flex: 1; color: #374151; }
/* 政策判读卡 */
.policy-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.policy-card { border: 1px solid #EDEFF2; border-radius: 6px; padding: 12px; }
.policy-title { font-size: 13px; font-weight: 600; color: #1F2937; margin-bottom: 8px; }
.policy-options { display: flex; flex-direction: column; gap: 6px; }
.policy-opt {
  display: flex; align-items: center; gap: 8px; padding: 7px 9px; border-radius: 5px;
  cursor: pointer; font-size: 12.5px; color: #374151; border: 1px solid #EDEFF2;
}
.policy-opt:hover { background: #F0F6FD; }
.policy-opt.on { background: #E3EDF9; border-color: #185FA5; color: #185FA5; font-weight: 600; }
.policy-dot { width: 8px; height: 8px; border-radius: 50%; background: #D1D5DB; flex: none; }
.policy-opt.on .policy-dot { background: #185FA5; }
.policy-label { line-height: 1.45; }
.policy-updated { margin-top: 8px; font-size: 11px; color: #909399; }
@media (max-width: 1100px) {
  .block-grid, .policy-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .block-grid, .policy-grid { grid-template-columns: 1fr; }
}
</style>
