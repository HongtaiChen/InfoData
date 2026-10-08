<script setup lang="ts">
/**
 * KpiCards —— 分析模块结论区（AnalysisShell §3）
 * 结论先行：大数字 + 状态词 + 口径 tooltip；红涨绿跌仅用于行情语义数值（tone='updown'）
 *
 * 分位与极值（2026-09-14 新增）：
 * - 分位 = 当前值在窗口内的排位（0=区间最低，100=最高）。
 *   绝对 pp 跨期不可比（2005 年的 5pp 与现在的 5pp 意义不同），只有分位才能回答「这是不是极端」。
 * - highlight = 分位进入极值区（<=10 / >=90）→ 左侧金色竖条 + 刻度圆点/文字金色。
 *   ⚠️ 金色只做「极值标记」，数值颜色不受影响——不拿金色去染涨跌数字，避免破坏 A 股铁律。
 * - anchor = 页内锚点 id：点卡片平滑滚到对应图表（结论 → 论据的下钻）。
 *
 * 配色三态（2026-09-19，与卡片墙一致）：
 * - tone='updown'  → 行情涨跌语义，红涨绿跌（指数 20 日收益、当日涨跌幅…）
 * - tone='diff'    → **组间收益差 / 相对强弱**（风偏分数、剪刀差、20 日超额），主色蓝 + 保留正负号。
 *   它不是「某个资产在涨跌」，染红绿会让「防守占优」看起来像独立警报。
 * - tone='neutral' → 无量纲量（分位、占比、离散度），主色蓝且不带正号
 * 详见 backend/app/analysis/registry.py 卡片墙契约第 ④ 条。
 *
 * 刻度条（2026-09-19）：`scale = {pct, label}` 把分位画成一根 0~100 的轨道。
 * ⚠️ label（窗口口径）必须来自后端 —— ERP 的窗口是「近 250 个月末」而非「近一年」，
 * 前端写死会把月频分位说成日频。没有历史序列的模块不下发 scale，此处不渲染。
 *
 * 风险调整（2026-09-15 新增）：
 * - adj = 收益差 ÷ 其自身近 250 日滚动标准差（σ 倍数）。与 pct 分工不同：
 *   pct 答「在近一年排第几」（纯相对排位，受区间选择影响），adj 答「偏离自身风险尺度几个单位」
 *   （含幅度、可跨期比较）。两者并列展示，互为参照。
 * - 配色：adj 不是涨跌语义 → 用次要文字色，绝不走红绿。
 */
import KpiHint from './KpiHint.vue'
import TermTip from './TermTip.vue'

interface Kpi {
  key: string
  label: string
  value: number | null
  unit?: string
  status?: string
  /** R11 影响句（P3）：后端下发的固定句式 `对权益：X · 对债：Y`（词表在后端一处定义）。
   *  ⚠️ 后端**方向不明确时会整键不写**（返回 null ⇒ 该字段不存在）—— 那时本行自然不渲染，
   *  不要在前端补 "对权益：中性"（那是"我们没判断"，与"判断为中性"是两件事）。 */
  impact?: string
  hint?: string
  /** R12 五段（P2）：后端 `hint_parts` 的有序段数组；存在时优先于 `hint`，缺省则回落后者。
   *  ⚠️ 长度**随 ⑤ 有无而变**（4 或 5）—— 这是后端"缺省即整键省略"的结果，前端不补位。 */
  hint_parts?: { key: string; text: string }[]
  /** R8 两轴（后端唯一注入点 `_tag()` 写入）：口径分类器。
   *  卡面**不直接渲染**这两个字段（前缀已由后端拼进 `label`）—— 在此登记是为了让
   *  「卡片消费了哪套口径」在类型层面可见，避免将来有人用 `label` 去反推地区。 */
  region?: string
  market?: string
  // 后端必须显式给出 tone：updown=红正绿负（行情语义）/ diff=组间收益差（主色蓝+带符号）
  // / neutral=主色（分位、占比等非涨跌语义）。缺失时按 updown 兜底，避免老接口把涨跌指标渲染成主色
  tone?: 'updown' | 'diff' | 'neutral'
  pct?: number | null
  /** 分位刻度条（pct + 窗口口径 label）；无历史序列的模块为 null/undefined → 不渲染 */
  scale?: { pct: number; label: string } | null
  highlight?: boolean
  anchor?: string
  /** 固定小数位（可选）：存量类读数（如「14.75 万亿美元」）需要尾零补齐，
   *  否则 `5.8` 与 `14.75` 并排会显得精度不一致。
   *  ⚠️ 这只是**格式化**，不做任何换算 —— 单位归一永远在后端完成（后端给 value + unit）。 */
  fmt_digits?: number
  // 风险调整值（σ 倍数）：仅风偏分数与大小盘剪刀差有，其余为 undefined
  adj?: number | null
}

const props = defineProps<{
  items: Kpi[]
  adjNote?: string
  /** R12 段定义（后端 `hint_segments`）——**原样透传**给 KpiHint。
   *  不传（另 4 个模块）时 KpiHint 走旧单段路径 ⇒ 零视觉变化。 */
  segments?: { key: string; label: string; order: number; required: boolean; group?: string }[]
}>()

/** 该卡是否有可看的口径说明 —— 有 `hint_parts`、无 `hint` 时入口也必须显示（否则浮窗被静默禁用） */
function hasHelp(k: Kpi): boolean {
  return !!(k.hint || k.hint_parts?.length)
}

/** 数值部分（不含单位）—— 单位要单独渲染，因为 `bp`/`pp` 需要包一层术语提示 */
function numText(k: Kpi): string {
  if (k.value == null) return '--'
  // diff 保留正负号（方向是它的信息），neutral 不带号（分位/占比没有方向）
  const sign = k.tone !== 'neutral' && k.value > 0 ? '+' : ''
  const num = k.fmt_digits != null ? Number(k.value).toFixed(k.fmt_digits) : String(k.value)
  return `${sign}${num}`
}
/** 单位原文（**保留前导空格**，如 ` 万亿美元`） */
function unitRaw(k: Kpi): string {
  return k.value == null ? '' : (k.unit ?? '')
}
/** 需要挂术语提示的量纲。⚠️ 只列 `glossary.ts` 里**已定义**的词 ——
 *  塞进一个未定义的词会被 TermTip 渲染成 `.tt-miss`，由断言抓出（不静默）。 */
const TIP_UNITS = new Set(['bp', 'pp'])
function unitTip(k: Kpi): string | null {
  const u = (k.unit ?? '').trim()
  return TIP_UNITS.has(u) ? u : null
}

/** 只有 tone='updown' 才是行情涨跌语义；diff（组间收益差）与 neutral 一律主色蓝 */
function valueClass(k: Kpi): string {
  if (k.value == null) return 'c-neutral'
  if (k.tone === 'diff' || k.tone === 'neutral') return 'c-primary'
  return k.value > 0 ? 'c-up' : k.value < 0 ? 'c-down' : 'c-neutral'
}
function fmt(k: Kpi): string {
  if (k.value == null) return '--'
  // diff 保留正负号（方向是它的信息），neutral 不带号（分位/占比没有方向）
  const sign = k.tone !== 'neutral' && k.value > 0 ? '+' : ''
  // fmt_digits：固定小数位（存量类「14.75 万亿美元」需要尾零，否则 5.8 与 14.75 精度不一致）
  const num = k.fmt_digits != null ? Number(k.value).toFixed(k.fmt_digits) : String(k.value)
  return `${sign}${num}${k.unit ?? ''}`
}
/** 刻度圆点位置：夹在 5~95% 内，避免圆点在两端被轨道裁掉半个（不改变读数，只改绘制） */
function dotLeft(pct: number): string {
  return `${Math.min(95, Math.max(5, pct))}%`
}
/** 风险调整值：以「σ（标准差）」为单位，正负号显式给出，便于与主值对照方向 */
function fmtAdj(v: number): string {
  return `${v > 0 ? '+' : ''}${v}σ`
}
function goAnchor(k: Kpi) {
  if (!k.anchor) return
  document.getElementById(k.anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <div class="kpi-row">
    <!-- 口径浮窗（2026-09-19 统一）：换用与总览页卡片墙同一个 KpiHint ——
         - 白底信息卡（naive-ui 默认深色，243 字的 ERP 在深色大段正文里更费眼）
         - `**强调**` 走 RichText（此前 {{ k.hint }} 直出，`**` 原样显示，是既有漏点）
         触发元素保留「整张卡片」：分析页卡片不可跳转，整卡悬浮更顺手，
         不必为了形式统一而要求用户去瞄准一个小图标。 -->
    <KpiHint
      v-for="k in props.items"
      :key="k.key"
      :label="k.label"
      :value="fmt(k)"
      :hint="k.hint"
      :parts="k.hint_parts"
      :segments="props.segments"
      :note="k.adj != null ? props.adjNote : undefined"
      placement="top"
    >
      <template #trigger>
        <div
          class="kpi-card"
          :class="{ 'kpi-card--hl': k.highlight, 'kpi-card--link': !!k.anchor }"
          @click="goAnchor(k)"
        >
          <div class="kpi-label">
            <span class="kpi-label-txt" :title="k.label">{{ k.label }}</span>
            <!-- 入口标记（P2 · 2026-09-27）：由纯斜体 `i` 改为带文字标签「口径」——
                 圆圈 i 的通用语义是"求助"，而这里的意思是"此读数有权重解释与异常阈值"；
                 且不悬浮时也要看得出"这里能点"（需求 R12.4 纪律）。
                 ⚠️ 判据含 `hint_parts`：有 parts 无 hint 的卡若不显示入口，浮窗虽在却无从发现。 -->
            <span v-if="hasHelp(k)" class="kpi-q">口径</span>
          </div>
          <div class="kpi-value" :class="valueClass(k)">{{ numText(k) }}<TermTip v-if="unitTip(k)" :term="unitTip(k)!" trigger="click" /><template v-else>{{ unitRaw(k) }}</template></div>
          <div class="kpi-status" v-if="k.status">{{ k.status }}</div>
          <!-- R11 影响句（P3）：**不与 status 合并** —— 合并会让 status 变成两义字段
               （判断词 + 资产含义），且卡片墙 V4 判据「status 不得与判读条逐字相同」会被搅浑。
               因果由口径浮窗的 ③「为什么」段承载（同源同方向），卡面只留标签。 -->
          <div class="kpi-impact" v-if="k.impact">{{ k.impact }}</div>
          <!-- 分位刻度条（2026-09-19）：把「这个数在窗口里排第几」直接画出来。
               窗口口径 label 由后端下发 —— ERP 是「近 250 个月末」，写死「近一年」就是口径错误 -->
          <div v-if="k.scale" class="kpi-scale">
            <span class="kpi-track">
              <span class="kpi-dot" :class="{ hl: k.highlight }" :style="{ left: dotLeft(k.scale.pct) }" />
            </span>
            <div class="kpi-scale-text" :class="{ hl: k.highlight }">
              {{ k.scale.label }} {{ k.scale.pct }}% <TermTip term="分位" trigger="click" />
            </div>
          </div>
          <div v-if="k.adj != null" class="kpi-adj">风险调整 {{ fmtAdj(k.adj) }}</div>
        </div>
      </template>
    </KpiHint>
  </div>
</template>

<style scoped>
.kpi-row { display: flex; gap: 12px; flex-wrap: wrap; }
.kpi-card {
  flex: 1; min-width: 170px; background: #fff; border: 1px solid #D4D7DE;
  border-radius: 8px; padding: 14px 16px; cursor: default; position: relative;
}
.kpi-card--link { cursor: pointer; }
.kpi-card--link:hover { border-color: #185FA5; }
/* 极值标记：仅左侧 3px 金条 + 分位文字金色，金色占比极小（「蓝骨金魂」：金色 ≤10% 强调） */
.kpi-card--hl::before {
  content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px;
  border-radius: 0 2px 2px 0; background: #C9A227;
}
.kpi-label { font-size: 13px; color: #6B7280; display: flex; align-items: center; gap: 4px; }
/* 文字包一层 span：label 行里同时有文字与 ⓘ 时，文字需可收缩但不被逐字挤压
   （CJK 可在任意字符处断行 —— 本项目踩过同类坑，窄卡片下会渲染成竖排） */
.kpi-label-txt { flex: 1 1 auto; min-width: 0; }
/* 口径入口（P2 · 2026-09-27）：由纯斜体 `i` 圆标改为**带文字标签**「口径」。两个理由：
   ① `i` / `?` 的通用语义是"求助"，而这里要说的是"该读数有权重解释与异常阈值"；
   ② 需求 R12.4 纪律要求"不悬浮也要有入口" —— 一个 14px 的 `i` 在正常视距下看不出可点。
   描边取 #8D97A5：实测 #B8BEC9 在这个字号下过淡、缩略图里几乎不可见。
   ⚠️ 本类被 5 个分析模块共用 ⇒ 这是一次**跨模块统一**（有意为之，不是副作用）；
      将来若要按模块区分形态，只能靠 props 而不是改这里。 */
.kpi-q {
  font-size: 11px; line-height: 1; color: #8D97A5; border: 1px solid #DDE3EA;
  border-radius: 3px; padding: 2px 4px; flex: none; white-space: nowrap;
}
.kpi-value { font-size: 26px; font-weight: 700; margin: 6px 0 4px; font-variant-numeric: tabular-nums; }
.kpi-status { font-size: 12px; color: #6B7280; }
/* R11 影响句：比 status 再轻一档（11.5px / #6B7280 同色但更小），避免与判断词抢注意力 */
.kpi-impact { font-size: 11.5px; color: #6B7280; margin-top: 2px; }
/* 分位刻度条：轨道 + 圆点 + 窗口文字（竖排，窗口文字较长时不挤压轨道） */
.kpi-scale { margin-top: 6px; }
.kpi-track { position: relative; display: block; height: 4px; border-radius: 2px; background: #E3E8EF; }
.kpi-dot {
  position: absolute; top: 50%; width: 8px; height: 8px; margin-left: -4px;
  border-radius: 50%; background: #185FA5; transform: translateY(-50%);
  box-shadow: 0 0 0 2px #fff;   /* 与 .kpi-card 底色同色，圆点压在轨道上有描边感 */
}
.kpi-dot.hl { background: #C9A227; }
.kpi-scale-text { font-size: 11px; color: #9CA3AF; margin-top: 4px; }
.kpi-scale-text.hl { color: #C9A227; font-weight: 500; }
/* 风险调整（σ 倍数）：非涨跌语义 → 次要文字色，不用红绿；与分位同为辅助读数 */
.kpi-adj { font-size: 11px; color: #6B7280; margin-top: 2px; font-variant-numeric: tabular-nums; }
.c-up { color: #EF232A; }
.c-down { color: #14B143; }
.c-primary { color: #185FA5; }
.c-neutral { color: #909399; }
</style>
