<script setup lang="ts">
/**
 * DomesticMoneyView —— 投资环境·国内货币（v1.0，2026-10-21）
 * 参照国内/国际指数/市场氛围/国际环境/全球货币风格，分两大块 + 左侧两级锚点导航：
 *   A. 技术面指标：
 *      ① 数量指标（货币供应总水位）  ② 价格指标（资金成本）  ③ 传导与市场反馈
 *   B. 基本面分析：
 *      ① 央行政策与制度  ② 财政政策与债务  ③ 国际收支与结构性因素  ④ 周期与长期趋势
 * 数据源：cn_liquidity_monthly（M0/M1/M2）/ cn_cb_balance_monthly（央行资产负债表）/
 *   interbank_rate_daily（SHIBOR/LPR）/ bond_profit_daily（国债收益率）/
 *   currency_boc_daily（人民币中间价）/ cn_reserve_monthly（外储）/
 *   dr_repo_daily（DR001/DR007，新采集）。
 * 单位：货币总量统一「亿元」（与央行公布口径一致），利率「%」。
 * 社融分项/信贷结构/存款总量免费源停更、超额准备金率/五篇大文章为季频披露——诚实标注缺口。
 * 结论由观看者判断：只呈现客观数值，不配定性词。红=涨、绿=跌（仅行情数字）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NCard, NSpin, NEmpty } from 'naive-ui'
import api from '../api'
import IndicatorBlock from '../components/analysis/IndicatorBlock.vue'
import HistoryCurve from '../components/analysis/HistoryCurve.vue'

const data = ref<any>(null)
const loading = ref(true)

// ---------- 左侧锚点导航 ----------
const NAV_ITEMS = [
  {
    id: 'sec-quantity', label: '技术面指标',
    children: [
      { id: 'sec-quantity', label: '数量指标（总水位）' },
      { id: 'sec-price', label: '价格指标（资金成本）' },
      { id: 'sec-transmission', label: '传导与市场反馈' },
    ],
  },
  {
    id: 'sec-cb', label: '基本面分析',
    children: [
      { id: 'sec-cb', label: '央行政策与制度' },
      { id: 'sec-fiscal', label: '财政政策与债务' },
      { id: 'sec-structure', label: '国际收支与结构' },
      { id: 'sec-cycle', label: '周期与长期趋势' },
    ],
  },
]
const activeNav = ref<string>('sec-quantity')

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
/** 亿元 → 万亿元 文本（如 3596289.7 → 359.6 万亿） */
function fmtYi(v: any, digits = 1): string {
  if (v == null) return '--'
  return (Number(v) / 10000).toFixed(digits) + ' 万亿'
}

// ---------- 数据加载 ----------
async function load() {
  loading.value = true
  try {
    data.value = await api.get('/environment/domestic-money') as any
  } catch (e) {
    console.error('[domestic money]', e)
  } finally {
    loading.value = false
  }
}

// ---------- 派生 ----------
const quantity = computed(() => data.value?.quantity)
const price = computed(() => data.value?.price)
const transmission = computed(() => data.value?.transmission)
const centralBank = computed(() => data.value?.central_bank)
const fiscal = computed(() => data.value?.fiscal)
const structure = computed(() => data.value?.structure)
const cycle = computed(() => data.value?.cycle)

// 定性卡（基本面用 note 型静态卡，直接内联渲染）
const CB_CARDS = computed(() => [
  { title: '货币政策基调', body: centralBank.value?.stance?.note },
  { title: '存款准备金率', body: `大型银行 ${centralBank.value?.rrr_large ?? '--'}%、中小型银行 ${centralBank.value?.rrr_small ?? '--'}%（加权平均约 6.6%，隐性下限约 5%，降准空间收窄）。` },
  { title: '利率走廊机制', body: centralBank.value?.rate_corridor?.note },
  { title: '结构性货币政策工具', body: centralBank.value?.structural_tools?.note },
])
const FISCAL_CARDS = computed(() => [
  { title: '财政政策协同', body: fiscal.value?.coordination?.note },
  { title: '政府债券融资', body: fiscal.value?.gov_bond?.note },
  { title: '融资平台债务化解', body: fiscal.value?.debt_resolution?.note },
])
const STRUCTURE_CARDS = computed(() => [
  { title: '货币创造模式变迁', body: structure.value?.money_creation?.note },
  { title: '经济结构转型', body: structure.value?.economic_transition?.note },
])
const CYCLE_CARDS = computed(() => [
  { title: '金融「脱实向虚」监管（MPA）', body: cycle.value?.mpa?.note },
  { title: '货币流通速度', body: cycle.value?.velocity?.note },
  { title: '数字货币投放', body: cycle.value?.digital_currency?.note },
])
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
        <p class="scope-p"><b>口径</b>：国内货币口径。技术面读人行货币供应（M0/M1/M2 + 同比）、央行资产负债表（基础货币/外汇占款/政府存款）、SHIBOR/LPR、国债收益率曲线、人民币中间价、外储，以及新采集的 DR001/DR007（中国货币网）。货币总量单位「亿元」，利率「%」。</p>
        <p class="scope-p"><b>呈现</b>：只呈现客观数值与历史走势，不配「宽松/收紧」等定性结论——极端与否由观看者结合当前位置自行判断。社融分项、信贷结构、存款总量免费源已停更（2026-04），超额准备金率、五篇大文章贷款为季频披露，均诚实标注缺口。红 = 涨、绿 = 跌（仅行情数字）。</p>
      </NCard>

      <NSpin :show="loading">
        <!-- ============ A. 技术面指标 ============ -->
        <!-- ① 数量指标（货币供应总水位） -->
        <div class="env-section" id="sec-quantity">
          <h2 class="env-h2">技术面指标</h2>
          <h3 class="env-h3">① 数量指标（货币供应总水位）</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="M2 广义货币"
              :value="quantity?.m2?.latest != null ? fmtYi(quantity.m2.latest, 2) : '--'"
              :note="'流通于经济体系的货币总量，涵盖 M0、M1 及各类存款。同比：' + (quantity?.m2_yoy?.latest != null ? fmtNum(quantity.m2_yoy.latest, 1) + '%' : '--') + '。M2 扩张时资金倾向流入风险资产。'"
            >
              <HistoryCurve
                :dates="quantity?.m2?.dates || []"
                :series="[{ name: 'M2(亿元)', values: quantity?.m2?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="M1 狭义货币"
              :value="quantity?.m1?.latest != null ? fmtYi(quantity.m1.latest, 2) : '--'"
              :note="'流通中现金 + 企业活期存款，反映现实购买力。同比：' + (quantity?.m1_yoy?.latest != null ? fmtNum(quantity.m1_yoy.latest, 1) + '%' : '--') + '。'"
            >
              <HistoryCurve
                :dates="quantity?.m1?.dates || []"
                :series="[{ name: 'M1(亿元)', values: quantity?.m1?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="M0 流通中现金"
              :value="quantity?.m0?.latest != null ? fmtYi(quantity.m0.latest, 2) : '--'"
              :note="'银行体系以外流通的现金。同比：' + (quantity?.m0_yoy?.latest != null ? fmtNum(quantity.m0_yoy.latest, 1) + '%' : '--') + '。'"
            >
              <HistoryCurve
                :dates="quantity?.m0?.dates || []"
                :series="[{ name: 'M0(亿元)', values: quantity?.m0?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock
              title="M1-M2 增速差"
              :value="quantity?.m1_m2_gap?.latest != null ? fmtNum(quantity.m1_m2_gap.latest, 1) + ' pp' : '--'"
              :note="'M1 增速 − M2 增速，反映资金活化程度。负值扩大 = 资金活化程度偏弱（定期化/储蓄化）。'"
            >
              <HistoryCurve
                :dates="quantity?.m1_m2_gap?.dates || []"
                :series="[{ name: 'M1-M2增速差(pp)', values: quantity?.m1_m2_gap?.values || [], color: '#C9A227' }]"
                height="240px" :zoom="true"
                :ref-lines="[{ value: 0, label: '0', color: '#9CA3AF' }]"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="货币乘数"
              :value="quantity?.money_mult?.latest != null ? fmtNum(quantity.money_mult.latest, 2) + '×' : '--'"
              :note="'派生口径：M2 ÷ 基础货币（央行资产负债表 reserve_money）。乘数上升 = 同样基础货币支撑更多信用，共同托住 M2 增速中枢。'"
            >
              <HistoryCurve
                :dates="quantity?.money_mult?.dates || []"
                :series="[{ name: '货币乘数', values: quantity?.money_mult?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="社会融资规模增量"
              :value="quantity?.shrzgm?.latest != null ? fmtNum(quantity.shrzgm.latest, 0) + ' 亿' : '数据源待接入'"
              :note="'社融增量（akshare macro_china_shrzgm）实测停更在 2026-04，5-9 月缺失。央行公告最新累计值（前三季度 27.46 万亿、9 月末存量 468.17 万亿）免费源无法自动同步。'"
            >
              <HistoryCurve
                v-if="quantity?.shrzgm?.values?.length"
                :dates="quantity?.shrzgm?.dates || []"
                :series="[{ name: '社融增量(亿)', values: quantity?.shrzgm?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
              <NEmpty v-else description="源侧停更（2026-04）" />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="超额准备金率" value="1.4%（2026Q1）" :note="quantity?.excess_reserve?.note">
              <NEmpty description="季频披露，无稳定序列" />
            </IndicatorBlock>
          </div>
        </div>

        <!-- ② 价格指标（资金成本） -->
        <div class="env-section" id="sec-price">
          <h3 class="env-h3">② 价格指标（资金成本）</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="DR001（隔夜质押回购）"
              :value="price?.dr001?.latest != null ? fmtNum(price.dr001.latest, 2) + '%' : '--'"
              :note="'银行间隔夜资金松紧最真实、最核心的「温度计」。2025 年起央行以 DR001 替代 DR007 作为核心观测指标。'"
            >
              <HistoryCurve
                :dates="price?.dr001?.dates || []"
                :series="[{ name: 'DR001', values: price?.dr001?.values || [], color: '#185FA5' }]"
                y-format="%" height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="DR007（7天质押回购）"
              :value="price?.dr007?.latest != null ? fmtNum(price.dr007.latest, 2) + '%' : '--'"
              :note="'银行间 7 天资金价格。低于 1.40% 的逆回购政策利率 = 资金面偏松。'"
            >
              <HistoryCurve
                :dates="price?.dr007?.dates || []"
                :series="[{ name: 'DR007', values: price?.dr007?.values || [], color: '#185FA5' }]"
                y-format="%" height="240px" :zoom="true"
                :ref-lines="[{ value: price?.reverse_repo_rate || 1.4, label: '逆回购 1.40%', color: '#9CA3AF' }]"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="SHIBOR（隔夜 / 1周）"
              :value="price?.shibor_on?.latest != null ? fmtNum(price.shibor_on.latest, 3) + '%' : '--'"
              :note="'单利、无担保、批发性利率。1 周：' + (price?.shibor_1w?.latest != null ? fmtNum(price.shibor_1w.latest, 3) + '%' : '--') + '。整体低位运行。'"
            >
              <HistoryCurve
                :dates="price?.shibor_on?.dates || []"
                :series="[
                  { name: '隔夜', values: price?.shibor_on?.values || [], color: '#185FA5' },
                  { name: '1周', values: price?.shibor_1w?.values || [], color: '#C9A227' },
                ]"
                y-format="%" height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock
              title="LPR（贷款市场报价利率）"
              :value="price?.lpr_1y?.latest != null ? fmtNum(price.lpr_1y.latest, 2) + '%' : '--'"
              :note="'贷款定价基准。1 年期 ' + (price?.lpr_1y?.latest != null ? fmtNum(price.lpr_1y.latest, 2) + '%' : '--') + '、5 年期以上 ' + (price?.lpr_5y?.latest != null ? fmtNum(price.lpr_5y.latest, 2) + '%' : '--') + '，已连续 16 个月保持不变。'"
            >
              <HistoryCurve
                :dates="price?.lpr_1y?.dates || []"
                :series="[
                  { name: '1Y', values: price?.lpr_1y?.values || [], color: '#185FA5' },
                  { name: '5Y+', values: price?.lpr_5y?.values || [], color: '#C9A227' },
                ]"
                y-format="%" height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="国债收益率曲线（10Y）"
              :value="price?.bond_10y?.latest != null ? fmtNum(price.bond_10y.latest, 2) + '%' : '--'"
              :note="'长端定价核心基准。2Y ' + (price?.bond_2y?.latest != null ? fmtNum(price.bond_2y.latest, 2) + '%' : '--') + '、10Y-2Y 利差 ' + (price?.bond_spread?.latest != null ? fmtNum(price.bond_spread.latest, 2) + ' pp' : '--') + '。央行 2024 年启动国债买卖后成为货币政策调控直接对象。'"
            >
              <HistoryCurve
                :dates="price?.bond_10y?.dates || []"
                :series="[
                  { name: '10Y', values: price?.bond_10y?.values || [], color: '#185FA5' },
                  { name: '2Y', values: price?.bond_2y?.values || [], color: '#C9A227' },
                ]"
                y-format="%" height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="逆回购利率（利率走廊中枢）"
              :value="price?.reverse_repo_rate != null ? fmtNum(price.reverse_repo_rate, 2) + '%' : '--'"
              :note="'央行 7 天逆回购操作利率，当前政策利率走廊中枢。2026-06 优化隔夜正逆回购操作机制，隔夜利率已成债市短端定价基准。'"
            >
              <div class="gap-box">
                <p class="gap-p">政策利率走廊：以 DR001 为核心、走廊收窄。7 天逆回购利率 {{ price?.reverse_repo_rate != null ? fmtNum(price.reverse_repo_rate, 2) + '%' : '--' }} 为中枢。</p>
              </div>
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="存贷款基准利率" value="2015-10 后未变" :note="price?.deposit_lending_benchmark?.note">
              <NEmpty description="已超 10 年未调整" />
            </IndicatorBlock>
          </div>
        </div>

        <!-- ③ 传导与市场反馈 -->
        <div class="env-section" id="sec-transmission">
          <h3 class="env-h3">③ 传导与市场反馈指标</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="人民币中间价（美元）"
              :value="transmission?.usd_mid?.latest != null ? fmtNum(transmission.usd_mid.latest, 4) : '--'"
              :note="'美元兑人民币中间价（元/美元）。保持人民币汇率在合理均衡水平上的基本稳定是货币政策重要考量。'"
            >
              <HistoryCurve
                :dates="transmission?.usd_mid?.dates || []"
                :series="[{ name: '美元/人民币', values: transmission?.usd_mid?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="外汇储备（亿美元）"
              :value="transmission?.fx_reserve_usd?.latest != null ? fmtNum(transmission.fx_reserve_usd.latest, 0) : '--'"
              :note="'中国外汇储备（人行官方，亿美元）。国际流动性缓冲与资本流动的反映。'"
            >
              <HistoryCurve
                :dates="transmission?.fx_reserve_usd?.dates || []"
                :series="[{ name: '外储(亿美元)', values: transmission?.fx_reserve_usd?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="其他存款（亿元）"
              :value="transmission?.other_deposit?.latest != null ? fmtYi(transmission.other_deposit.latest, 2) : '--'"
              :note="'银行体系资金来源（库内「其他存款」口径）。「人民币存款余额总量」口径缺，见下方缺口标注。'"
            >
              <HistoryCurve
                :dates="transmission?.other_deposit?.dates || []"
                :series="[{ name: '其他存款(亿元)', values: transmission?.other_deposit?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="信贷结构（企业 vs 住户）" value="数据源待接入" :note="transmission?.credit_structure?.note">
              <NEmpty description="源侧停更" />
            </IndicatorBlock>
            <IndicatorBlock title="直接融资占比" value="数据源待接入" :note="transmission?.direct_financing?.note">
              <NEmpty description="依赖停更的社融分项" />
            </IndicatorBlock>
            <IndicatorBlock title="「五篇大文章」贷款" value="季频披露" :note="transmission?.five_articles?.note">
              <NEmpty description="无免费月度序列" />
            </IndicatorBlock>
          </div>
        </div>

        <!-- ============ B. 基本面分析 ============ -->
        <div class="env-section" id="sec-cb">
          <h2 class="env-h2">基本面分析</h2>

          <!-- ① 央行政策与制度 -->
          <h3 class="env-h3">① 央行政策与制度</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="存款准备金率（大/中小行）"
              :value="centralBank?.rrr_large != null ? centralBank.rrr_large + '% / ' + centralBank.rrr_small + '%' : '--'"
              :note="'大型银行 ' + (centralBank?.rrr_large ?? '--') + '%、中小型银行 ' + (centralBank?.rrr_small ?? '--') + '%（加权平均约 6.6%）。降准 = 释放长期流动性。'"
            >
              <div class="gap-box">
                <p class="gap-p">加权平均准备金率已降至约 6.6%，隐性下限约 5%，降准空间逐步收窄，未来降准将主要对大行产生效果。</p>
              </div>
            </IndicatorBlock>

            <IndicatorBlock
              title="人民银行资产负债表（总资产）"
              :value="structure?.fx_reserve?.latest != null ? fmtYi(structure.fx_reserve.latest, 2) : '--'"
              :note="'央行资产负债表外汇占款（亿元）。外汇占款对基础货币贡献下降，央行主动投放工具重要性上升。'"
            >
              <HistoryCurve
                :dates="structure?.fx_reserve?.dates || []"
                :series="[{ name: '外汇占款(亿元)', values: structure?.fx_reserve?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="财政存款（政府存款）"
              :value="fiscal?.gov_deposit?.latest != null ? fmtYi(fiscal.gov_deposit.latest, 2) : '--'"
              :note="'央行资产负债表政府存款（亿元）。财政存款增减直接影响超额准备金水平，是超储率测算的重要因素。'"
            >
              <HistoryCurve
                :dates="fiscal?.gov_deposit?.dates || []"
                :series="[{ name: '政府存款(亿元)', values: fiscal?.gov_deposit?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <div class="policy-grid">
            <div v-for="c in CB_CARDS" :key="c.title" class="policy-card">
              <div class="policy-title">{{ c.title }}</div>
              <div class="policy-body">{{ c.body }}</div>
            </div>
          </div>

          <!-- ② 财政政策与债务 -->
          <h3 class="env-h3" id="sec-fiscal">② 财政政策与债务</h3>
          <div class="policy-grid">
            <div v-for="c in FISCAL_CARDS" :key="c.title" class="policy-card">
              <div class="policy-title">{{ c.title }}</div>
              <div class="policy-body">{{ c.body }}</div>
            </div>
          </div>

          <!-- ③ 国际收支与结构性因素 -->
          <h3 class="env-h3" id="sec-structure">③ 国际收支与结构性因素</h3>
          <div class="policy-grid">
            <div v-for="c in STRUCTURE_CARDS" :key="c.title" class="policy-card">
              <div class="policy-title">{{ c.title }}</div>
              <div class="policy-body">{{ c.body }}</div>
            </div>
          </div>

          <!-- ④ 周期与长期趋势 -->
          <h3 class="env-h3" id="sec-cycle">④ 周期与长期趋势</h3>
          <div class="block-grid">
            <IndicatorBlock title="M2/GDP 比率" value="见说明" :note="cycle?.m2_gdp?.note">
              <NEmpty description="库内无绝对 GDP，无法序列化" />
            </IndicatorBlock>
          </div>
          <div class="policy-grid">
            <div v-for="c in CYCLE_CARDS" :key="c.title" class="policy-card">
              <div class="policy-title">{{ c.title }}</div>
              <div class="policy-body">{{ c.body }}</div>
            </div>
          </div>
        </div>
      </NSpin>
    </div>
  </div>
</template>

<style scoped>
/* 页内导航 + 内容两栏布局；与国内/国际指数/市场氛围/国际环境/全球货币页同款 */
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
.gap-p { font-size: 13px; line-height: 1.7; color: #374151; margin: 0 0 12px; }
/* 定性判读卡（展示型，纯说明） */
.policy-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 10px; }
.policy-card { border: 1px solid #EDEFF2; border-radius: 6px; padding: 12px; }
.policy-title { font-size: 13px; font-weight: 600; color: #1F2937; margin-bottom: 8px; }
.policy-body { font-size: 12.5px; line-height: 1.65; color: #374151; }
@media (max-width: 1100px) {
  .block-grid, .policy-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .block-grid, .policy-grid { grid-template-columns: 1fr; }
}
</style>
