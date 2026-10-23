<script setup lang="ts">
/**
 * GlobalMoneyView —— 投资环境·全球货币（v1.0，2026-10-21）
 * 参照国内/国际指数/市场氛围/国际环境风格，分两大块 + 左侧两级锚点导航：
 *   A. 技术面指标：
 *      ① 数量指标（货币供应总水位）  ② 价格指标（资金成本）  ③ 传导与市场反馈
 *   B. 基本面分析：
 *      ① 央行政策与制度  ② 财政政策与债务  ③ 国际收支与结构性因素  ④ 周期与长期趋势
 * 数据源：复用 v2.1 三轨制已落地的货币流动性表（美 M1M2/美联储资产负债表/纽约联储 SOFR/
 *   欧元区 M2/中国 M0M1M2/央行资产负债表/外储/BIS GLI + 政策利率/DXY/VIX/商品）。
 * 合成口径（全球 M2 汇总、美元净流动性）标「合成口径·非官方」；
 * TED/LIBOR-OIS/SOFR-IORB/XCCY/MOVE/EMBI/新兴资本流/COFER 等免费源不可得，诚实标注缺口。
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
/** 十亿美元 → 万亿美元 文本（如 94945.1 → 94.9 万亿） */
function fmtTn(v: any, digits = 1): string {
  if (v == null) return '--'
  return (Number(v) / 1000).toFixed(digits) + ' 万亿'
}
/** 截至日期 → 「截至 YYYY-MM-DD」；无则空串 */
function asOfLabel(d: any): string {
  return d ? `截至 ${String(d).slice(0, 10)}` : ''
}

// ---------- 数据加载 ----------
async function load() {
  loading.value = true
  try {
    data.value = await api.get('/environment/global-money') as any
  } catch (e) {
    console.error('[global money]', e)
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

// 债务/GDP 国家名映射
const DEBT_NAME: Record<string, string> = {
  USA: '美国', CHN: '中国', JPN: '日本', DEU: '德国', GBR: '英国', FRA: '法国',
}
const GDP_NAME: Record<string, string> = {
  USA: '美国', CHN: '中国', JPN: '日本', DEU: '德国', GBR: '英国', FRA: '法国',
}

/** 取某国指标序列的末值（债务/GDP 或 GDP 增速） */
function lastOf(countries: any[] | undefined, code: string): number | null {
  const c = countries?.find((x: any) => x.country_code === code)
  if (!c?.values?.length) return null
  return Number(c.values[c.values.length - 1])
}
const usDebtGdp = computed(() => lastOf(fiscal.value?.debt_gdp?.countries, 'USA'))
const usGdpGrowth = computed(() => lastOf(structure.value?.gdp_growth?.countries, 'USA'))
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
        <p class="scope-p"><b>口径</b>：全球货币口径。技术面读 v2.1 三轨制已落地的货币流动性表（美 M1M2 / 美联储资产负债表 / 纽约联储 SOFR / 欧元区 M2 / 中国 M0M1M2 / 央行资产负债表 / 外储 / BIS GLI）+ 复用政策利率 / DXY / VIX / 商品。</p>
        <p class="scope-p"><b>呈现</b>：只呈现客观数值与历史走势，不配「宽松/收紧」等定性结论——极端与否由观看者结合当前位置自行判断。合成口径（全球 M2 汇总、美元净流动性）已标「合成口径·非官方」。红 = 涨、绿 = 跌（仅行情数字）。</p>
      </NCard>

      <NSpin :show="loading">
        <!-- ============ A. 技术面指标 ============ -->
        <!-- ① 数量指标（货币供应总水位） -->
        <div class="env-section" id="sec-quantity">
          <h2 class="env-h2">技术面指标</h2>
          <h3 class="env-h3">① 数量指标（货币供应总水位）</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="全球 M2 汇总（美欧中，折美元）"
              :as-of="asOfLabel(quantity?.gbl_m2?.latest_date)"
              :value="quantity?.gbl_m2?.latest != null ? fmtTn(quantity.gbl_m2.latest, 1) : '--'"
              :note="'合成口径·非官方：美/欧/中三大经济体 M2 按汇率折美元加总（缺日本 M2）。M2 扩张时资金倾向流入风险资产。'"
            >
              <HistoryCurve
                :dates="quantity?.gbl_m2?.dates || []"
                :series="[{ name: '全球M2(十亿美元)', values: quantity?.gbl_m2?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="美元净流动性（Fed − TGA − ON RRP）"
              :as-of="asOfLabel(quantity?.net_liquidity?.latest_date)"
              :value="quantity?.net_liquidity?.latest != null ? fmtTn(quantity.net_liquidity.latest, 1) : '--'"
              :note="'合成口径·非官方：美联储总资产 − 财政部一般账户(TGA) − 隔夜逆回购(ON RRP)，过滤暂时性扭曲，反映真正在金融体系中流通的美元。'"
            >
              <HistoryCurve
                :dates="quantity?.net_liquidity?.dates || []"
                :series="[{ name: '净流动性(十亿美元)', values: quantity?.net_liquidity?.values || [], color: '#C9A227' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="美联储资产负债表（总资产）"
              :as-of="asOfLabel(quantity?.fed_balance?.latest_date)"
              :value="quantity?.fed_balance?.latest != null ? fmtTn(quantity.fed_balance.latest, 2) : '--'"
              :note="'美联储 H.4.1 总资产（DBnomics 主口径 + 官网交叉校验）。扩表 = 主动注入流动性，缩表 = 回收流动性。'"
            >
              <HistoryCurve
                :dates="quantity?.fed_balance?.dates || []"
                :series="[{ name: '总资产(十亿美元)', values: quantity?.fed_balance?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock
              title="美国 M2"
              :as-of="asOfLabel(quantity?.us_m2?.latest_date)"
              :value="quantity?.us_m2?.latest != null ? fmtTn(quantity.us_m2.latest, 2) : '--'"
              :note="'美国 M2（十亿美元，DBnomics FED/H6）。同比：' + (quantity?.us_m2_yoy?.latest != null ? fmtNum(quantity.us_m2_yoy.latest, 1) + '%' : '--')"
            >
              <HistoryCurve
                :dates="quantity?.us_m2?.dates || []"
                :series="[{ name: '美M2(十亿)', values: quantity?.us_m2?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="欧元区 M2（折美元）"
              :as-of="asOfLabel(quantity?.eu_m2?.latest_date)"
              :value="quantity?.eu_m2?.latest != null ? fmtTn(quantity.eu_m2.latest, 1) : '--'"
              :note="'欧元区 M2（DBnomics ECB/BSI，百万欧元）× 欧元/美元汇率折算。'"
            >
              <HistoryCurve
                :dates="quantity?.eu_m2?.dates || []"
                :series="[{ name: '欧M2(十亿)', values: quantity?.eu_m2?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="中国 M2（折美元）"
              :as-of="asOfLabel(quantity?.cn_m2?.latest_date)"
              :value="quantity?.cn_m2?.latest != null ? fmtTn(quantity.cn_m2.latest, 1) : '--'"
              :note="'中国 M2（人行统计，亿元）÷ 人民币中间价折算。同比：' + (quantity?.cn_m2_yoy?.latest != null ? fmtNum(quantity.cn_m2_yoy.latest, 1) + '%' : '--')"
            >
              <HistoryCurve
                :dates="quantity?.cn_m2?.dates || []"
                :series="[{ name: '中M2(十亿)', values: quantity?.cn_m2?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock
              title="中国人民银行总资产（折美元）"
              :as-of="asOfLabel(quantity?.cn_cb_balance?.latest_date)"
              :value="quantity?.cn_cb_balance?.latest != null ? fmtTn(quantity.cn_cb_balance.latest, 1) : '--'"
              :note="'人行资产负债表总资产（亿元）÷ 人民币中间价折算。央行主动投放（MLF/逆回购）扩张 = 放水。'"
            >
              <HistoryCurve
                :dates="quantity?.cn_cb_balance?.dates || []"
                :series="[{ name: '人行总资产(十亿)', values: quantity?.cn_cb_balance?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="货币乘数（美 M2 ÷ 基础货币）"
              :as-of="asOfLabel(quantity?.money_mult?.latest_date)"
              :value="quantity?.money_mult?.latest != null ? fmtNum(quantity.money_mult.latest, 2) + '×' : '--'"
              :note="'美国 M2 ÷ 基础货币，衡量基础货币转化为广义货币的效率。乘数上升 = 同样基础货币支撑更多信用。'"
            >
              <HistoryCurve
                :dates="quantity?.money_mult?.dates || []"
                :series="[{ name: '货币乘数', values: quantity?.money_mult?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="央行资产 / GDP 比例"
              :value="'见参考值'"
              :note="'央行流动性投放强度的相对指标。库内无绝对 GDP，仅给静态参考值（2026 年中）。'"
            >
              <NCard size="small" class="ov-card">
                <div class="val-table">
                  <div class="val-row val-head">
                    <span class="val-name">央行</span>
                    <span class="val-cell">资产/GDP</span>
                  </div>
                  <div v-for="s in (quantity?.cb_gdp?.static || [])" :key="s.central_bank" class="val-row">
                    <span class="val-name">{{ s.central_bank }}</span>
                    <span class="val-cell">{{ s.ratio }}%</span>
                  </div>
                </div>
              </NCard>
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ quantity?.note }}</div>
        </div>

        <!-- ② 价格指标（资金成本） -->
        <div class="env-section" id="sec-price">
          <h3 class="env-h3">② 价格指标（资金成本）</h3>
          <NCard size="small" class="ov-card">
            <div class="val-table">
              <div class="val-row val-head">
                <span class="val-name">央行</span>
                <span class="val-cell">政策利率</span>
                <span class="val-cell">变动(bp)</span>
                <span class="val-cell">决议日期</span>
              </div>
              <div v-for="r in (price?.policy_rate || [])" :key="r.country" class="val-row">
                <span class="val-name">{{ r.country }}</span>
                <span class="val-cell">{{ r.rate != null ? fmtNum(r.rate, 2) + '%' : '--' }}</span>
                <span class="val-cell" :style="{ color: r.change_bp > 0 ? '#EF232A' : (r.change_bp < 0 ? '#14B143' : '#1F2937') }">
                  {{ r.change_bp != null ? (r.change_bp > 0 ? '+' : '') + r.change_bp : '--' }}
                </span>
                <span class="val-cell">{{ r.event_date }}</span>
              </div>
            </div>
            <div class="ov-note">利率差：美−欧 {{ price?.rate_diff?.us_eu != null ? price.rate_diff.us_eu + ' bp' : '--' }} ｜ 美−日 {{ price?.rate_diff?.us_jp != null ? price.rate_diff.us_jp + ' bp' : '--' }}</div>
          </NCard>

          <div class="block-grid">
            <IndicatorBlock
              title="SOFR（担保隔夜融资利率）"
              :as-of="asOfLabel(price?.sofr?.latest_date)"
              :value="price?.sofr?.latest != null ? fmtNum(price.sofr.latest, 2) + '%' : '--'"
              :note="'LIBOR 官方替代利率（纽约联储直连）。美国货币市场实际成交利率，比政策利率更真实。'"
            >
              <HistoryCurve
                :dates="price?.sofr?.dates || []"
                :series="[{ name: 'SOFR', values: price?.sofr?.values || [], color: '#185FA5' }]"
                y-format="%" height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="VIX（标普500 隐含波动率）"
              :as-of="asOfLabel(price?.vix?.latest_date)"
              :value="price?.vix?.latest != null ? fmtNum(price.vix.latest, 2) : '--'"
              :note="'恐慌指数：<15 平稳，>25-30 风险信号。流动性收紧常伴随 VIX 飙升。'"
            >
              <HistoryCurve
                :dates="price?.vix?.dates || []"
                :series="[{ name: 'VIX', values: price?.vix?.values || [], color: '#185FA5' }]"
                :ref-lines="[{ value: 25, label: '25 恐慌', color: '#EF232A' }, { value: 15, label: '15 平稳', color: '#14B143' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock title="TED 利差" value="数据源待接入" :note="price?.ted?.note">
              <NEmpty description="LIBOR 已停发" />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="LIBOR-OIS 利差" value="数据源待接入" :note="price?.libor_ois?.note">
              <NEmpty description="LIBOR 已停发" />
            </IndicatorBlock>
            <IndicatorBlock title="SOFR-IORB 利差" value="数据源待接入" :note="price?.sofr_iorb?.note">
              <NEmpty description="IORB 需 FRED（受阻）" />
            </IndicatorBlock>
            <IndicatorBlock title="跨货币互换基差 XCCY" value="数据源待接入" :note="price?.xccy?.note">
              <NEmpty description="Bloomberg/ICE 商业授权" />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="MOVE（美债波动率）" value="数据源待接入" :note="price?.move?.note">
              <NEmpty description="ICE 商业授权" />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ price?.note }}</div>
        </div>

        <!-- ③ 传导与市场反馈 -->
        <div class="env-section" id="sec-transmission">
          <h3 class="env-h3">③ 传导与市场反馈指标</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="美元指数 DXY"
              :as-of="asOfLabel(transmission?.dxy?.latest_date)"
              :value="transmission?.dxy?.latest != null ? fmtNum(transmission.dxy.latest, 2) : '--'"
              :note="'全球避险与流动性风向：美元走强 = 全球流动性收紧的危险信号，走弱 = 压力缓释。'"
            >
              <HistoryCurve
                :dates="transmission?.dxy?.dates || []"
                :series="[{ name: 'DXY', values: transmission?.dxy?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="黄金（沪金主力）"
              :as-of="asOfLabel(transmission?.gold?.latest_date)"
              :value="transmission?.gold?.latest != null ? fmtNum(transmission.gold.latest, 1) : '--'"
              :note="'流动性宽松 + 风险偏好提升时，避险与需求双驱动金价上涨。'"
            >
              <HistoryCurve
                :dates="transmission?.gold?.dates || []"
                :series="[{ name: '黄金', values: transmission?.gold?.values || [], color: '#C9A227' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="铜（沪铜主力）"
              :as-of="asOfLabel(transmission?.copper?.latest_date)"
              :value="transmission?.copper?.latest != null ? fmtNum(transmission.copper.latest, 0) : '--'"
              :note="'流动性宽松时需求扩张驱动铜价（铜是全球工业需求风向标）。原油库内无直接期货，其波动率见国际环境章节 OVX。'"
            >
              <HistoryCurve
                :dates="transmission?.copper?.dates || []"
                :series="[{ name: '铜(元/吨)', values: transmission?.copper?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock
              title="中国外汇储备（亿美元）"
              :as-of="asOfLabel(transmission?.reserve?.latest_date)"
              :value="transmission?.reserve?.latest != null ? fmtNum(transmission.reserve.latest, 0) : '--'"
              :note="'外汇储备（1978 起）：国际流动性缓冲与资本流动的反映。'"
            >
              <HistoryCurve
                :dates="transmission?.reserve?.dates || []"
                :series="[{ name: '外储(亿美元)', values: transmission?.reserve?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock title="全球股市估值中枢" value="见国际指数章节" :note="transmission?.valuation?.note">
              <NEmpty description="见「国际指数·基本面」块" />
            </IndicatorBlock>

            <IndicatorBlock title="新兴市场资本流动" value="数据源待接入" :note="transmission?.em_flow?.note">
              <NEmpty description="IIF 订阅" />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="主权外债利差 EMBI" value="数据源待接入" :note="transmission?.embi?.note">
              <NEmpty description="J.P. Morgan 商业产品" />
            </IndicatorBlock>
            <IndicatorBlock title="全球外储币种构成 COFER" value="数据源待接入" :note="transmission?.cofer?.note">
              <NEmpty description="IMF 季频（暂缓）" />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ transmission?.note }}</div>
        </div>

        <!-- ============ B. 基本面分析 ============ -->
        <div class="env-section" id="sec-cb">
          <h2 class="env-h2">基本面分析</h2>

          <!-- ① 央行政策与制度 -->
          <h3 class="env-h3" id="sec-cb">① 央行政策与制度</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="美联储扩表/缩表"
              :as-of="asOfLabel(centralBank?.fed_balance?.latest_date)"
              :value="centralBank?.fed_balance?.latest != null ? fmtTn(centralBank.fed_balance.latest, 2) : '--'"
              :note="'美联储总资产：扩表注入流动性，缩表回收流动性。QE/QT 的直接观测。'"
            >
              <HistoryCurve
                :dates="centralBank?.fed_balance?.dates || []"
                :series="[{ name: '总资产(十亿美元)', values: centralBank?.fed_balance?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="人民银行扩表/缩表"
              :as-of="asOfLabel(centralBank?.cn_cb_balance?.latest_date)"
              :value="centralBank?.cn_cb_balance?.latest != null ? fmtTn(centralBank.cn_cb_balance.latest, 1) : '--'"
              :note="'人行总资产：主动投放（MLF/逆回购）扩张 = 放水，收缩 = 收水。'"
            >
              <HistoryCurve
                :dates="centralBank?.cn_cb_balance?.dates || []"
                :series="[{ name: '总资产(十亿美元)', values: centralBank?.cn_cb_balance?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="利率差（美 − 欧 / 美 − 日）"
              :value="centralBank?.rate_diff?.us_eu != null ? centralBank.rate_diff.us_eu + ' / ' + centralBank.rate_diff.us_jp + ' bp' : '--'"
              :note="'各国政策利率之差：驱动套利交易与资本流动，影响汇率与全球资金配置。'"
            >
              <div class="gap-box">
                <p class="gap-p">利率差为时点值（bp）：美−欧 {{ centralBank?.rate_diff?.us_eu != null ? centralBank.rate_diff.us_eu : '--' }}，美−日 {{ centralBank?.rate_diff?.us_jp != null ? centralBank.rate_diff.us_jp : '--' }}。利率差扩大 = 套利交易驱动资金流向高息货币。</p>
              </div>
            </IndicatorBlock>
          </div>
          <div class="policy-grid">
            <div v-for="j in (centralBank?.judgements || [])" :key="j.topic" class="policy-card">
              <div class="policy-title">{{ j.title }}</div>
              <div class="policy-options">
                <div v-for="s in j.states" :key="s" class="policy-opt-static">
                  <span class="policy-dot" />
                  <span class="policy-label">{{ s }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="ov-note">📖 {{ centralBank?.note }}</div>

          <!-- ② 财政政策与债务 -->
          <h3 class="env-h3" id="sec-fiscal">② 财政政策与债务</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="美国政府债务 / GDP"
              :value="usDebtGdp != null ? fmtNum(usDebtGdp, 0) + '%' : '--'"
              :note="'IMF WEO（年度含预测）。美国联邦债务/GDP 已从 35% 以下升至 120% 以上，长期侵蚀美元信用。'"
            >
              <NCard size="small" class="ov-card">
                <div class="val-table">
                  <div class="val-row val-head">
                    <span class="val-name">经济体</span>
                    <span class="val-cell">债务/GDP</span>
                    <span class="val-cell">年份</span>
                  </div>
                  <div v-for="c in (fiscal?.debt_gdp?.countries || [])" :key="c.country_code" class="val-row">
                    <span class="val-name">{{ DEBT_NAME[c.country_code] || c.country_code }}</span>
                    <span class="val-cell">{{ c.values?.length ? fmtNum(c.values[c.values.length - 1], 0) + '%' : '--' }}</span>
                    <span class="val-cell">{{ c.years?.length ? c.years[c.years.length - 1] : '--' }}</span>
                  </div>
                </div>
              </NCard>
            </IndicatorBlock>

            <IndicatorBlock
              title="财政赤字规模"
              value="数据源待接入"
              :note="'政府收支缺口：财政赤字货币化（发债创造货币）直接影响货币供应。免费源为美财政部 fiscaldata（可采，本期暂缓）。'"
            >
              <NEmpty description="IMF/财政部（暂缓）" />
            </IndicatorBlock>

            <IndicatorBlock title="财政主导风险 / MMT 实践" value="定性判读" :note="'财政赤字对央行施压、现代货币理论实践——定性议题，无免费结构化序列。'">
              <div class="gap-box">
                <p class="gap-p">见下方定性判读卡。</p>
              </div>
            </IndicatorBlock>
          </div>
          <div class="policy-grid">
            <div v-for="j in (fiscal?.judgements || [])" :key="j.topic" class="policy-card">
              <div class="policy-title">{{ j.title }}</div>
              <div class="policy-options">
                <div v-for="s in j.states" :key="s" class="policy-opt-static">
                  <span class="policy-dot" />
                  <span class="policy-label">{{ s }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="ov-note">📖 {{ fiscal?.note }}</div>

          <!-- ③ 国际收支与结构性因素 -->
          <h3 class="env-h3" id="sec-structure">③ 国际收支与结构性因素</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="外汇储备积累（中国）"
              :as-of="asOfLabel(structure?.reserve?.latest_date)"
              :value="structure?.reserve?.latest != null ? fmtNum(structure.reserve.latest, 0) + ' 亿美元' : '--'"
              :note="'新兴市场央行积累美元储备并投资美债，压低美债收益率，间接影响全球流动性。'"
            >
              <HistoryCurve
                :dates="structure?.reserve?.dates || []"
                :series="[{ name: '外储(亿美元)', values: structure?.reserve?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="货币乘数 / 流通速度"
              :as-of="asOfLabel(structure?.money_mult?.latest_date)"
              :value="structure?.money_mult?.latest != null ? fmtNum(structure.money_mult.latest, 2) + '×' : '--'"
              :note="'基础货币转化为广义货币的效率。流通速度上升 = 同样基础货币支撑更多经济活动。'"
            >
              <HistoryCurve
                :dates="structure?.money_mult?.dates || []"
                :series="[{ name: '货币乘数', values: structure?.money_mult?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="全球 GDP 增速（IMF WEO）"
              :value="usGdpGrowth != null ? fmtNum(usGdpGrowth, 1) + '%' : '--'"
              :note="'全球 GDP 是驱动全球流动性的基本面因素之一。实体经济对货币的需求。'"
            >
              <NCard size="small" class="ov-card">
                <div class="val-table">
                  <div class="val-row val-head">
                    <span class="val-name">经济体</span>
                    <span class="val-cell">GDP 增速</span>
                    <span class="val-cell">年份</span>
                  </div>
                  <div v-for="c in (structure?.gdp_growth?.countries || [])" :key="c.country_code" class="val-row">
                    <span class="val-name">{{ GDP_NAME[c.country_code] || c.country_code }}</span>
                    <span class="val-cell">{{ c.values?.length ? fmtNum(c.values[c.values.length - 1], 1) + '%' : '--' }}</span>
                    <span class="val-cell">{{ c.years?.length ? c.years[c.years.length - 1] : '--' }}</span>
                  </div>
                </div>
              </NCard>
            </IndicatorBlock>
          </div>
          <div class="policy-grid">
            <div v-for="j in (structure?.judgements || [])" :key="j.topic" class="policy-card">
              <div class="policy-title">{{ j.title }}</div>
              <div class="policy-options">
                <div v-for="s in j.states" :key="s" class="policy-opt-static">
                  <span class="policy-dot" />
                  <span class="policy-label">{{ s }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="ov-note">📖 {{ structure?.note }}</div>

          <!-- ④ 周期与长期趋势 -->
          <h3 class="env-h3" id="sec-cycle">④ 周期与长期趋势</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="全球流动性指标 GLI（离岸美元信贷）"
              :as-of="asOfLabel(cycle?.gli?.latest_date)"
              :value="cycle?.gli?.latest != null ? fmtTn(cycle.gli.latest, 2) : '--'"
              :note="'BIS 全球流动性指标（官方口径，严格 6 条件：非美借款人 × 非银行 × 跨境+本地外币 × 贷款+债券 × 美元）。GLI 呈约 65 个月（5.5 年）振荡周期。'"
            >
              <HistoryCurve
                :dates="cycle?.gli?.dates || []"
                :series="[{ name: 'GLI(十亿美元)', values: cycle?.gli?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <div class="policy-grid">
            <div v-for="j in (cycle?.judgements || [])" :key="j.topic" class="policy-card">
              <div class="policy-title">{{ j.title }}</div>
              <div class="policy-options">
                <div v-for="s in j.states" :key="s" class="policy-opt-static">
                  <span class="policy-dot" />
                  <span class="policy-label">{{ s }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="ov-note">📖 {{ cycle?.note }}</div>
        </div>
      </NSpin>
    </div>
  </div>
</template>

<style scoped>
/* 页内导航 + 内容两栏布局；与国内/国际指数/市场氛围/国际环境页同款 */
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
/* 定性判读卡（展示型两态，无落库交互） */
.policy-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 10px; }
.policy-card { border: 1px solid #EDEFF2; border-radius: 6px; padding: 12px; }
.policy-title { font-size: 13px; font-weight: 600; color: #1F2937; margin-bottom: 8px; }
.policy-options { display: flex; flex-direction: column; gap: 6px; }
.policy-opt-static { display: flex; align-items: center; gap: 8px; padding: 7px 9px; font-size: 12.5px; color: #374151; }
.policy-dot { width: 8px; height: 8px; border-radius: 50%; background: #D1D5DB; flex: none; }
.policy-label { line-height: 1.45; }
/* 数值表 */
.val-table { width: 100%; }
.val-row { display: flex; align-items: center; border-bottom: 1px solid #F0F2F5; padding: 8px 6px; }
.val-row.val-head { border-bottom: 2px solid #E5E9EF; font-weight: 600; color: #6B7280; font-size: 12px; }
.val-name { flex: 1; font-size: 13px; color: #374151; }
.val-head .val-name { color: #6B7280; }
.val-cell { flex: 0 0 110px; text-align: right; font-size: 13px; color: #1F2937; font-variant-numeric: tabular-nums; }
.val-head .val-cell { color: #6B7280; }
@media (max-width: 1100px) {
  .block-grid, .policy-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .block-grid, .policy-grid { grid-template-columns: 1fr; }
}
</style>
