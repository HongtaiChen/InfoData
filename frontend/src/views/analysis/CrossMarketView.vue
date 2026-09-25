<script setup lang="ts">
/**
 * CrossMarketView —— 跨市场对照模块（分析研究·市场风向）
 * 数据：GET /api/analysis/cross-market
 *
 * 落的是设计规范 §1.0 的 ① **同类横比**（美股/中国香港/A股 之间谁强谁弱）、
 * ③ **基准超额**（A股相对美股是真强还是普跌里跌得少）、② **自身纵比**（各市场 20 日收益
 * 处于自己近一年什么位置），核心是 ④ **交叉印证**：隔夜美股 ↔ 次日 A股 ——
 * 把"外围影响"从叙事变成一个可跟踪的分位指标。
 *
 * ⚠️ 三条口径纪律（后端已处理，前端只透传）：
 * 1. **各市场交易日不同步**：后端按**日期并集 + 前值填充**对齐（不是按下标对齐，
 *    按下标会把相差近一个月的日期画成同一时刻，错位方向恰好会被读成"美股领先 A股"）。
 * 2. **隔夜传导只用「标普500 × 中证全指」这一对**：恒生与 A股 交易时段部分重叠，
 *    "隔夜"含义不同，混算会把两个机制平均掉。
 * 3. **同向率与相关系数分工不同**：同向率答"跟不跟"（有直接行为含义），
 *    相关系数答"跟多紧"（含幅度），两个都要看。
 */
import { computed, onMounted, ref } from 'vue'
import { NCard, NDatePicker, NSelect, NSpin } from 'naive-ui'
import KpiCards from '../../components/analysis/KpiCards.vue'
import DualLineTrend from '../../components/analysis/DualLineTrend.vue'
import DrillLink from '../../components/analysis/DrillLink.vue'
import RichText from '../../components/analysis/RichText.vue'
import api from '../../api'

interface Kpi {
  key: string; label: string; value: number | null; unit?: string
  status?: string; hint?: string
  tone?: 'updown' | 'diff' | 'neutral'
  card_rank?: number | null
  scale?: { pct: number; label: string } | null
  pct?: number | null; highlight?: boolean; anchor?: string
}
interface Market {
  code: string; name: string; region: string
  last_close: number | null; last_date: string | null; start_date: string | null; rows: number
  change_pct: number | null; ret_20: number | null; ret_60: number | null; ret_20_pct: number | null
}
interface Gap { code: string; name: string; region: string; gap_pp: number | null }
interface Overnight {
  same_rate: number | null; same_rate_250: number | null; same_rate_pct: number | null
  corr: number | null; avg_overseas: number | null; avg_local: number | null
  pairs: number; series: { dates: string[]; values: (number | null)[] }
}

const loading = ref(false)
const asOf = ref('')
const isReplay = ref(false)
const kpis = ref<Kpi[]>([])
const cn = ref<Market | null>(null)
const markets = ref<Market[]>([])
const gaps = ref<Gap[]>([])
const overnight = ref<Overnight | null>(null)
const chart = ref<{ dates: string[]; series: { name: string; code: string; color: string
  values: (number | null)[]; base_date: string }[] }>({ dates: [], series: [] })
const note = ref('')

const replayTs = ref<number | null>(null)
const trendDays = ref(500)
const trendOptions = [
  { label: '近 120 日', value: 120 },
  { label: '近 250 日', value: 250 },
  { label: '近 500 日', value: 500 },
]

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = { trend_days: trendDays.value }
    if (replayTs.value) {
      const d = new Date(replayTs.value)
      params.as_of = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    }
    const resp: any = await api.get('/analysis/cross-market', { params })
    asOf.value = resp.as_of ?? ''
    isReplay.value = !!resp.is_replay
    note.value = resp.note ?? ''
    kpis.value = (resp.kpis ?? []).map((k: Kpi) => ({ ...k, tone: k.tone ?? 'updown' }))
    cn.value = resp.cn ?? null
    markets.value = resp.markets ?? []
    gaps.value = resp.gaps ?? []
    overnight.value = resp.overnight ?? null
    chart.value = resp.chart ?? { dates: [], series: [] }
  } catch (e) {
    console.error('[cross-market]', e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

/** 隔夜传导的滚动同向率（单序列；50% 是"完全无关"的中性线，由 DualLineTrend 的零轴替代说明） */
const overnightSeries = computed(() => {
  const s = overnight.value?.series
  if (!s) return []
  return [{ name: '滚动 60 日同向率', values: s.values, color: '#185FA5' }]
})

/** 收益正负着色：只有真正的行情涨跌才走红涨绿跌（A 股铁律） */
function pctCls(v: number | null): string {
  if (v == null) return 'color:#909399'
  return v > 0 ? 'color:#EF232A' : v < 0 ? 'color:#14B143' : 'color:#909399'
}
function fmtPct(v: number | null): string {
  return v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}
/** 超额：跨市场收益差 → 主色蓝 + 保留符号（契约第 ④ 条，不染红绿） */
function fmtGap(v: number | null): string {
  return v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(2)}pp`
}
/** 某市场「相对 A股 超额」= 该市场 20 日收益 − A股 20 日收益 = 后端 gap_pp 取反
 *  （后端 gap_pp 定义为「A股 − 该市场」，故这里必须取反，否则符号与列名相反） */
function gapOf(code: string): number | null {
  const g = gaps.value.find((x) => x.code === code)
  if (!g || g.gap_pp == null) return null
  return -g.gap_pp
}
</script>

<template>
  <NSpin :show="loading">
    <div class="cm-params">
      <span class="cm-asof">
        数据截至 <b>{{ asOf || '--' }}</b>
        <span class="cm-sub">（A股基准腿 {{ cn?.name ?? '--' }}；各市场交易日不同步，走势图按日期对齐）</span>
        <span v-if="isReplay" class="cm-replay">历史回放</span>
      </span>
      <div class="cm-params-right">
        <NDatePicker v-model:value="replayTs" type="date" size="small" clearable
                     placeholder="回放某交易日" style="width: 148px" @update:value="load" />
        <NSelect v-model:value="trendDays" :options="trendOptions" size="tiny"
                 style="width: 130px" @update:value="load" />
      </div>
    </div>

    <KpiCards :items="kpis" />

    <!-- 归一化走势：跨市场同轴可比 -->
    <NCard id="cm-trend" size="small" class="cm-card"
           title="归一化走势对照（各自起点 = 100，按日期对齐 + 前值填充）">
      <template #header-extra>
        <DrillLink :items="[{ label: '行情看板看大盘', to: '/market' }]" />
      </template>
      <DualLineTrend :dates="chart.dates" :series="chart.series" height="320px" />
      <div class="cm-legend">
        <span v-for="s in chart.series" :key="s.code" class="cm-legend-item">
          <i class="cm-dot" :style="{ background: s.color }" />{{ s.name }}
          <span class="cm-dim">（起点 {{ s.base_date }}）</span>
        </span>
      </div>
      <div class="cm-foot">
        ⚠️ 绝对点位跨期不可比，故统一归一化为 100。半透明段表示该市场当日无交易（沿用最近一次收盘）。
      </div>
    </NCard>

    <!-- ③ 基准超额 -->
    <NCard id="cm-gap" size="small" class="cm-card"
           title="基准超额（A股中证全指 20 日收益 − 各市场同期 20 日收益）">
      <div class="cm-hint">
        正值不一定代表“我们强”——普跌行情里跌得少也会是正超额。
        务必与 A股 自身的 20 日收益成对阅读。
      </div>
      <table class="cm-table">
        <thead>
          <tr><th>市场</th><th>区间</th><th>20 日收益</th><th>相对A股超额</th><th>20日收益近一年分位</th></tr>
        </thead>
        <tbody>
          <tr class="cm-row--self">
            <td><b>{{ cn?.name }}</b></td>
            <td class="cm-dim">A股基准腿</td>
            <td :style="pctCls(cn?.ret_20 ?? null)"><b>{{ fmtPct(cn?.ret_20 ?? null) }}</b></td>
            <td class="cm-dim">—</td>
            <td class="cm-dim">{{ cn?.ret_20_pct == null ? '--' : cn.ret_20_pct + '%' }}</td>
          </tr>
          <tr v-for="m in markets" :key="m.code">
            <td>{{ m.name }}</td>
            <td class="cm-dim">{{ m.region }}</td>
            <td :style="pctCls(m.ret_20)">{{ fmtPct(m.ret_20) }}</td>
            <td class="cm-gap">{{ fmtGap(gapOf(m.code)) }}</td>
            <td class="cm-dim">{{ m.ret_20_pct == null ? '--' : m.ret_20_pct + '%' }}</td>
          </tr>
        </tbody>
      </table>
      <div class="cm-foot">
        超额列 = 该市场 20 日收益 − A股 20 日收益（故 A股 行显示"—"）。
        「近一年分位」是该市场 20 日收益在<b>自身</b>近 250 个 20 日收益观测中的排位（重叠窗口，与市场风向同口径）。
      </div>
    </NCard>

    <!-- ④ 交叉印证：隔夜传导 -->
    <NCard id="cm-overnight" size="small" class="cm-card"
           title="隔夜传导（标普500 前夜收益 ↔ 中证全指次日收益）">
      <div class="cm-stats">
        <span>滚动 60 日同向率 <b class="cm-strong">{{ overnight?.same_rate ?? '--' }}%</b></span>
        <span>近 250 日同向率 <b class="cm-strong">{{ overnight?.same_rate_250 ?? '--' }}%</b></span>
        <span>相关系数 <b class="cm-strong">{{ overnight?.corr ?? '--' }}</b></span>
        <span>配对交易日 <b>{{ overnight?.pairs ?? '--' }}</b></span>
        <span class="cm-dim">分位 {{ overnight?.same_rate_pct ?? '--' }}%（近一年）</span>
      </div>
      <DualLineTrend v-if="overnight?.series" :dates="overnight.series.dates"
                     :series="overnightSeries" height="240px" />
      <div class="cm-foot">
        同向率 50% = 完全无关；持续高于 60% 说明外部信息确实在传导。
        ⚠️ 它衡量“跟不跟”，不衡量幅度（幅度看相关系数）。零轴不代表“中性”，
        同向率的中性线是 50%。
      </div>
      <div class="cm-cross">
        <span class="cm-cross-badge">读法</span>
        <span class="cm-cross-text">
          隔夜均值：美股 <b>{{ overnight?.avg_overseas ?? '--' }}%</b> / A股
          <b>{{ overnight?.avg_local ?? '--' }}%</b>（近 250 个配对交易日）。
          两者之差说明"外部涨跌能否等幅传到 A股"——通常 A股 的日波动显著小于美股，
          故同向率高 ≠ 幅度相当。
        </span>
      </div>
    </NCard>

    <!-- 明细：标题不再列举三个窗口（那是功能清单，表头已自带列名） -->
    <NCard id="cm-detail" size="small" class="cm-card" title="各市场明细">
      <table class="cm-table">
        <thead>
          <tr><th>市场</th><th>最新收盘</th><th>日期</th><th>当日</th><th>20 日</th><th>60 日</th><th>样本起点</th></tr>
        </thead>
        <tbody>
          <tr class="cm-row--self">
            <td><b>{{ cn?.name }}</b></td>
            <td class="cm-num">{{ cn?.last_close ?? '--' }}</td>
            <td class="cm-dim">{{ cn?.last_date ?? '--' }}</td>
            <td :style="pctCls(cn?.change_pct ?? null)">{{ fmtPct(cn?.change_pct ?? null) }}</td>
            <td :style="pctCls(cn?.ret_20 ?? null)">{{ fmtPct(cn?.ret_20 ?? null) }}</td>
            <td :style="pctCls(cn?.ret_60 ?? null)">{{ fmtPct(cn?.ret_60 ?? null) }}</td>
            <td class="cm-dim">{{ cn?.start_date ?? '--' }}</td>
          </tr>
          <tr v-for="m in markets" :key="m.code">
            <td>{{ m.name }} <span class="cm-dim">{{ m.region }}</span></td>
            <td class="cm-num">{{ m.last_close ?? '--' }}</td>
            <td class="cm-dim">{{ m.last_date ?? '--' }}</td>
            <td :style="pctCls(m.change_pct)">{{ fmtPct(m.change_pct) }}</td>
            <td :style="pctCls(m.ret_20)">{{ fmtPct(m.ret_20) }}</td>
            <td :style="pctCls(m.ret_60)">{{ fmtPct(m.ret_60) }}</td>
            <td class="cm-dim">{{ m.start_date ?? '--' }}</td>
          </tr>
        </tbody>
      </table>
      <div class="cm-foot">
        「当日」是各市场<b>自己当地交易日</b>的涨跌，不是同一个自然日 —— 各市场时区不同，
        这里不做对齐，只作侧面参照。
      </div>
      <div class="cm-note"><RichText :text="note" /></div>
    </NCard>
  </NSpin>
</template>

<style scoped>
.cm-params { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.cm-asof { font-size: 13px; color: #6B7280; }
.cm-asof b { color: #1F2937; }
.cm-sub { color: #9CA3AF; }
.cm-replay {
  margin-left: 6px; font-size: 11px; color: #185FA5;
  background: #E6F1FB; border-radius: 3px; padding: 1px 5px;
}
.cm-params-right { display: flex; gap: 8px; align-items: center; }
.cm-card { margin-bottom: 12px; scroll-margin-top: 12px; }
.cm-hint { font-size: 12px; color: #6B7280; line-height: 1.7; margin-bottom: 10px; }
.cm-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.cm-table th {
  text-align: left; font-weight: 500; color: #9CA3AF; padding: 6px 8px;
  border-bottom: 1px solid #EDEFF2;
}
.cm-table td { padding: 7px 8px; border-bottom: 1px solid #F5F7FA; color: #374151; }
.cm-row--self { background: #F8FAFC; }
.cm-gap { color: #185FA5; font-variant-numeric: tabular-nums; }
.cm-num { font-variant-numeric: tabular-nums; }
.cm-dim { color: #9CA3AF; }
.cm-strong { color: #185FA5; font-variant-numeric: tabular-nums; }
.cm-stats {
  display: flex; gap: 18px; flex-wrap: wrap; align-items: baseline;
  font-size: 12px; color: #6B7280; margin-bottom: 12px;
  padding-bottom: 10px; border-bottom: 1px dashed #EDEFF2;
}
.cm-legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: #6B7280; margin-top: 6px; }
.cm-legend-item { display: inline-flex; align-items: center; gap: 5px; }
.cm-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.cm-foot { font-size: 11px; color: #9CA3AF; margin-top: 10px; line-height: 1.65; }
/* 页脚为**模板内静态文案**（非后端下发），故用 <b> 而非 `**`：`**` 只由 RichText 解析，
   写在模板里会原样渲染（2026-09-25 全站扫描实测漏点）。 */
.cm-foot b { color: #1F2937; font-weight: 600; }
.cm-cross {
  display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: 6px;
  border: 1px solid #EDEFF2; border-left: 3px solid #185FA5; margin-top: 10px;
}
.cm-cross-badge {
  font-size: 11px; border-radius: 3px; padding: 1px 6px; flex: none; margin-top: 1px;
  background: #E6F1FB; color: #185FA5;
}
.cm-cross-text { font-size: 12px; color: #374151; line-height: 1.7; }
.cm-note { font-size: 11px; color: #9CA3AF; margin-top: 10px; line-height: 1.65; }
</style>
