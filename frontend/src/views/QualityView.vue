<script setup lang="ts">
import { ref, computed, onMounted, watch, h } from 'vue'
import {
  NCard, NGrid, NGi, NStatistic, NButton, NDataTable, NTag, NSelect,
  NInput, NSpace, NEmpty, NTabs, NTabPane, NProgress, NSwitch, NSpin, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'

const message = useMessage()
const route = useRoute()
const router = useRouter()

// ---------- 工具 ----------
const checkTypeCn: Record<string, string> = {
  freshness_daily: '新鲜度·交易日历',
  freshness_interval: '新鲜度·距当前',
  date_floor: '日期下限',
  row_count_slice: '行数·最新日',
  row_count_total: '行数·总量',
  null_rate_slice: '空值率·最新日',
  violation_count: '脏数据计数',
  where_count: '条件计数',
  regex_count: '格式校验',
  unique_index: '唯一索引结构',
  gap_scan: '全史缺口扫描',
  source_handoff: '跨源衔接',
  per_key_coverage: '每票覆盖率',
  recon: '外部对账',
}
const fmtTime = (t: string) => (t ? String(t).replace('T', ' ').slice(0, 19) : '--')

// 频率：日检（每日 20:30） / 周检（周一 21:30）
const groupText = (g: string) => (g === 'weekly' ? '周检' : '日检')
function groupTag(g: string) {
  return h(
    NTag,
    { size: 'small', type: g === 'weekly' ? 'warning' : 'default', bordered: false },
    () => groupText(g),
  )
}
const GROUP_OPTIONS = [
  { label: '全部频率', value: '' },
  { label: '日检（每日 20:30）', value: 'daily' },
  { label: '周检（周一 21:30）', value: 'weekly' },
]


function statusTag(status: string) {
  const map: Record<string, { type: 'success' | 'warning' | 'error' | 'info'; text: string }> = {
    pass: { type: 'success', text: '通过' },
    warning: { type: 'warning', text: '提醒' },
    fail: { type: 'error', text: '异常' },
    error: { type: 'error', text: '执行错误' },
  }
  const m = map[status] || { type: 'info', text: status }
  return h(NTag, { size: 'small', type: m.type, bordered: false }, () => m.text)
}
function sevTag(sev: string) {
  const map: Record<string, { type: 'error' | 'warning' | 'default'; text: string }> = {
    critical: { type: 'error', text: '严重' },
    warning: { type: 'warning', text: '一般' },
    info: { type: 'default', text: '提示' },
  }
  const m = map[sev] || { type: 'default', text: sev }
  return h(NTag, { size: 'small', type: m.type, bordered: false }, () => m.text)
}

// ---------- 表注释映射 ----------
const tableComment = ref<Record<string, string>>({})
async function loadTableComments() {
  try {
    const resp: any = await api.get('/db/tables', { silent: true })
    for (const t of resp.items || []) tableComment.value[t.name] = t.comment || ''
  } catch (e) {
    /* 忽略：注释仅作增强展示 */
  }
}
const tableLabel = (name: string) => {
  const c = tableComment.value[name]
  return c ? `${c}（${name}）` : name
}

// ---------- 最新一轮概况 ----------
const summary = ref<any>(null)
const summaryLoading = ref(false)
async function loadSummary() {
  summaryLoading.value = true
  try {
    const resp: any = await api.get('/dq/summary')
    summary.value = resp
  } catch (e) {
    console.error('[dq summary]', e)
  } finally {
    summaryLoading.value = false
  }
}
const passCnt = computed(() => summary.value?.counts?.pass || 0)
const warnCnt = computed(() => summary.value?.counts?.warning || 0)
const failCnt = computed(() => summary.value?.counts?.fail || 0)
const errCnt = computed(() => summary.value?.counts?.error || 0)
const totalRules = computed(() => summary.value?.total_rules || 0)
const passRate = computed(() =>
  totalRules.value > 0 ? Math.round((passCnt.value / totalRules.value) * 1000) / 10 : 0,
)
const criticalFail = computed(
  () => (summary.value?.issues || []).filter((x: any) => x.severity === 'critical').length,
)

// ---------- 本轮明细 ----------
const reportItems = ref<any[]>([])
const reportLoading = ref(false)
// 从 URL query 初始化（上下文穿透：从某表质量弹窗跳转时自动锁到该表）
const initialTable = typeof route.query.table === 'string' ? route.query.table : ''
const tableFilter = ref(initialTable)
const statusFilter = ref('')
const groupFilter = ref('')
const ruleSearch = ref('')
async function loadReport() {
  reportLoading.value = true
  try {
    const resp: any = await api.get('/dq/report')
    reportItems.value = resp.items || []
  } catch (e) {
    console.error('[dq report]', e)
  } finally {
    reportLoading.value = false
  }
}
const tableOptions = computed(() => {
  const names = [...new Set(reportItems.value.map((r) => r.table_name))]
  return [
    { label: '全部表', value: '' },
    ...names.map((n) => ({ label: tableLabel(n), value: n })),
  ]
})
const filteredReport = computed(() => {
  let rows = reportItems.value
  if (tableFilter.value) rows = rows.filter((r) => r.table_name === tableFilter.value)
  if (groupFilter.value) rows = rows.filter((r) => (r.rule_group || 'daily') === groupFilter.value)
  if (statusFilter.value === 'warn_fail') rows = rows.filter((r) => ['warning', 'fail', 'error'].includes(r.status))
  else if (statusFilter.value) rows = rows.filter((r) => r.status === statusFilter.value)
  const kw = ruleSearch.value.trim().toLowerCase()
  if (kw) rows = rows.filter((r) => (r.rule_name + r.table_name + r.message).toLowerCase().includes(kw))
  return rows
})

const reportColumns: DataTableColumns<any> = [
  {
    title: '表',
    key: 'table_name',
    width: 200,
    render: (r) => h('div', { style: 'line-height:1.4' }, [
      h('div', { style: 'font-weight:500;color:#1f2937' }, r.table_name),
      tableComment.value[r.table_name]
        ? h('div', { style: 'font-size:12px;color:#9ca3af' }, tableComment.value[r.table_name])
        : null,
    ]),
  },
  { title: '频率', key: 'rule_group', width: 70, render: (r) => groupTag(r.rule_group) },
  {
    title: '规则',
    key: 'rule_name',
    width: 190,
    render: (r) => h('span', { style: 'font-family:ui-monospace,Consolas,monospace;font-size:12px' }, r.rule_name),
  },
  {
    title: '检查类型',
    key: 'check_type',
    width: 120,
    render: (r) => checkTypeCn[r.check_type] || r.check_type,
  },
  { title: '严重级', key: 'severity', width: 76, render: (r) => sevTag(r.severity) },
  { title: '结果', key: 'status', width: 86, render: (r) => statusTag(r.status) },
  { title: '实测值', key: 'metric_value', width: 110 },
  {
    title: '结果时间',
    key: 'run_at',
    width: 150,
    render: (r) => h('span', { style: 'font-size:12px;color:#6b7280' }, fmtTime(r.run_at)),
  },
  { title: '判定说明', key: 'message', ellipsis: { tooltip: { width: 420 } } },
]


// ---------- 规则配置 ----------
const rulesItems = ref<any[]>([])
const rulesLoading = ref(false)
const ruleGroupFilter = ref('')
async function loadRules() {
  rulesLoading.value = true
  try {
    const resp: any = await api.get('/dq/rules')
    rulesItems.value = resp.items || []
  } catch (e) {
    console.error('[dq rules]', e)
  } finally {
    rulesLoading.value = false
  }
}
async function toggleRule(r: any, val: boolean) {
  try {
    await api.put(`/dq/rules/${r.rule_name}`, { enabled: val })
    message.success(val ? `已启用 ${r.rule_name}` : `已停用 ${r.rule_name}`)
    r.enabled = val
  } catch (e: any) {
    message.error(e?.detail || '更新失败')
  }
}
const filteredRules = computed(() => {
  if (!ruleGroupFilter.value) return rulesItems.value
  return rulesItems.value.filter((r) => (r.rule_group || 'daily') === ruleGroupFilter.value)
})
const rulesColumns: DataTableColumns<any> = [
  { title: 'ID', key: 'id', width: 56 },
  {
    title: '规则',
    key: 'rule_name',
    width: 185,
    render: (r) => h('span', { style: 'font-family:ui-monospace,Consolas,monospace;font-size:12px' }, r.rule_name),
  },
  { title: '频率', key: 'rule_group', width: 70, render: (r) => groupTag(r.rule_group) },
  {
    title: '表',
    key: 'table_name',
    width: 200,
    render: (r) => h('div', { style: 'line-height:1.4' }, [
      h('div', { style: 'font-weight:500;color:#1f2937' }, r.table_name),
      tableComment.value[r.table_name]
        ? h('div', { style: 'font-size:12px;color:#9ca3af' }, tableComment.value[r.table_name])
        : null,
    ]),
  },
  { title: '检查类型', key: 'check_type', width: 120, render: (r) => checkTypeCn[r.check_type] || r.check_type },
  { title: '严重级', key: 'severity', width: 76, render: (r) => sevTag(r.severity) },
  {
    title: '最近结果',
    key: 'last_status',
    width: 88,
    render: (r) => (r.last_status ? statusTag(r.last_status) : '--'),
  },
  {
    title: '结果时间',
    key: 'last_run_at',
    width: 148,
    render: (r) =>
      r.last_run_at
        ? h('span', { style: 'font-size:12px;color:#6b7280' }, fmtTime(r.last_run_at))
        : h('span', { style: 'font-size:12px;color:#c0c4cc' }, '尚未执行'),
  },
  {
    title: '最近实测值',
    key: 'last_metric',
    width: 110,
    render: (r) => h('span', { style: 'font-size:12px' }, r.last_metric || '--'),
  },
  {
    title: '启用',
    key: 'enabled',
    width: 76,
    render: (r) =>
      h(NSwitch, {
        size: 'small',
        value: !!r.enabled,
        'onUpdate:value': (v: boolean) => toggleRule(r, v),
      }),
  },
  { title: '规则说明', key: 'description', ellipsis: { tooltip: { width: 460 } } },
]


// ---------- 对账记录（L2 外部对账 + L1/L3 全史缺口待办）----------
const recon = ref<any>(null)
const reconLoading = ref(false)
const reconRunAt = ref('')
async function loadRecon() {
  reconLoading.value = true
  try {
    const params: any = { limit: 10 }
    if (reconRunAt.value) params.run_at = reconRunAt.value
    const resp: any = await api.get('/dq/recon', { params })
    recon.value = resp
    reconRunAt.value = resp.run_at || ''
  } catch (e) {
    console.error('[dq recon]', e)
  } finally {
    reconLoading.value = false
  }
}
watch(reconRunAt, () => loadRecon())
const MODE_CN: Record<string, string> = { recon_window: '窗口·腾讯', recon_sample: '抽样·东财' }
const reconRoundOptions = computed(() =>
  (recon.value?.rounds || []).map((r: any) => ({
    label: `${r.run_at} · ${MODE_CN[r.rule_name] || r.rule_name} · ${
      r.status === 'pass' ? '无差异' : '有差异'
    }`,
    value: r.run_at,
  })),
)
const diffTypeCn: Record<string, string> = {
  latest_lag: '采集时延',
  missing_local: '本地缺失',
  missing_remote: '远端缺失(疑停牌)',
  value_diff: '数值不符',
  count_diff: '行数不符',
  range_start_diff: '起始日不符',
  range_end_diff: '结束日不符',
  range_diff: '区间不符',
}
function diffTypeTag(t: string) {
  const isLag = t === 'latest_lag'
  return h(
    NTag,
    { size: 'small', type: isLag ? 'info' : 'warning', bordered: false },
    () => diffTypeCn[t] || t,
  )
}
const lagCount = computed(() =>
  (recon.value?.stats || [])
    .filter((s: any) => s.diff_type === 'latest_lag')
    .reduce((a: number, s: any) => a + s.c, 0),
)
const realDiffCount = computed(() =>
  (recon.value?.stats || [])
    .filter((s: any) => s.diff_type !== 'latest_lag')
    .reduce((a: number, s: any) => a + s.c, 0),
)
const reconDetailColumns: DataTableColumns<any> = [
  { title: '股票', key: 'stock_code', width: 90 },
  { title: '交易日', key: 'trade_date', width: 110, render: (r) => r.trade_date || '--' },
  { title: '差异类型', key: 'diff_type', width: 140, render: (r) => diffTypeTag(r.diff_type) },
  { title: '字段', key: 'col_name', width: 74, render: (r) => r.col_name || '--' },
  { title: '本地值', key: 'local_value', width: 200 },
  { title: '远端值', key: 'remote_value', width: 200 },
  { title: '外部源', key: 'remote_source', width: 90 },
  { title: '说明', key: 'note', ellipsis: { tooltip: { width: 380 } } },
]
const gapColumns: DataTableColumns<any> = [
  { title: '股票', key: 'stock_code', width: 90 },
  { title: '缺口起', key: 'prev_date', width: 110 },
  { title: '缺口止', key: 'next_date', width: 110 },
  { title: '间隔天数', key: 'gap_days', width: 96 },
  {
    title: '风险',
    key: 'risk',
    width: 84,
    render: (r) =>
      h(NTag, { size: 'small', type: r.risk === 'high' ? 'error' : 'default', bordered: false },
        () => (r.risk === 'high' ? '高危' : r.risk === 'suspect' ? '疑似' : '一般')),
  },
  {
    title: '状态',
    key: 'status',
    width: 84,
    render: (r) =>
      h(NTag, { size: 'small', type: r.status === 'fixed' ? 'success' : 'warning', bordered: false },
        () => (r.status === 'fixed' ? '已修复' : '待修复')),
  },
]

// ---------- 近 14 天趋势 ----------
const history = ref<any[]>([])
async function loadHistory() {
  try {
    const resp: any = await api.get('/dq/history', { params: { days: 14 }, silent: true })
    history.value = resp.items || []
  } catch (e) {
    console.error('[dq history]', e)
  }
}
const historyDays = computed(() => {
  const map: Record<string, { pass: number; warning: number; fail: number; error: number }> = {}
  for (const it of history.value) {
    const d = String(it.run_date).slice(5)
    if (!map[d]) map[d] = { pass: 0, warning: 0, fail: 0, error: 0 }
    const s = it.status as 'pass' | 'warning' | 'fail' | 'error'
    map[d][s] = it.c
  }
  return Object.entries(map).map(([d, v]) => ({ d, ...v }))
})
const barMax = computed(() =>
  Math.max(1, ...historyDays.value.map((x) => x.pass + x.warning + x.fail + x.error)),
)

// ---------- 立即体检 ----------
const triggering = ref(false)
const pollingTimer = ref<number | null>(null)
async function triggerNow() {
  if (triggering.value) return
  triggering.value = true
  try {
    const resp: any = await api.post('/dq/trigger')
    message.success(resp.message || '体检已提交，正在执行…')
    const prevRunAt = summary.value?.run_at || ''
    const deadline = Date.now() + 30000
    const wait = async () => {
      await loadSummary()
      if (summary.value?.run_at && summary.value.run_at !== prevRunAt) {
        await Promise.all([loadReport(), loadHistory()])
        message.success('本轮体检完成')
        triggering.value = false
        return
      }
      if (Date.now() > deadline) {
        triggering.value = false
        message.warning('体检仍在执行中，可稍后手动刷新查看结果')
        return
      }
      pollingTimer.value = window.setTimeout(wait, 2000)
    }
    await wait()
  } catch (e: any) {
    message.error(e?.detail || '触发失败，请稍后再试')
    triggering.value = false
  }
}

// ---------- 加载 ----------
const lastUpdate = ref('')
async function refreshAll() {
  await Promise.all([loadSummary(), loadReport(), loadRules(), loadHistory(), loadRecon()])
  lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

onMounted(() => {
  loadTableComments()
  refreshAll()
})

// 双向同步：表筛选变化时同步回 URL，刷新/分享链接可恢复现场
watch(tableFilter, (v) => {
  const cur = typeof route.query.table === 'string' ? route.query.table : ''
  if (v === cur) return
  router.replace({ query: v ? { ...route.query, table: v } : (() => { const { table: _, ...rest } = route.query; return rest })() })
})
</script>

<template>
  <div>
    <!-- 顶部操作行 -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <NSpace align="center" :size="10">
        <NTag v-if="summary?.run_at" size="small" type="info" :bordered="false">
          最近体检 {{ fmtTime(summary.run_at) }} · {{ summary.run_date }}
        </NTag>
        <NTag v-else size="small" type="warning" :bordered="false">尚未体检</NTag>
        <NTag size="small" :bordered="false" style="color:#999">日检 每日 20:30 · 周检 周一 21:30 · 各规则取最近一次结果</NTag>
        <span v-if="lastUpdate" style="color:#bbb;font-size:12px">更新于 {{ lastUpdate }}</span>
      </NSpace>
      <NSpace>
        <NButton size="small" secondary type="primary" @click="refreshAll()">刷新</NButton>
        <NButton size="small" type="primary" :loading="triggering" @click="triggerNow">立即体检</NButton>
      </NSpace>
    </div>

    <!-- 无数据兜底 -->
    <NCard v-if="!summary?.run_at" style="margin-bottom:12px">
      <NEmpty description="还没有体检记录。点击右上角「立即体检」开始第一轮检查">
        <template #extra>
          <NButton type="primary" size="small" @click="triggerNow">立即体检</NButton>
        </template>
      </NEmpty>
    </NCard>

    <template v-else>
      <!-- 统计卡片 -->
      <NGrid :cols="5" :x-gap="12" style="margin-bottom:12px">
        <NGi>
          <NCard size="small" hoverable>
            <NStatistic label="规则总数" :value="totalRules">
              <template #suffix>条</template>
            </NStatistic>
          </NCard>
        </NGi>
        <NGi>
          <NCard size="small" hoverable>
            <NStatistic label="通过" :value="passCnt" style="--n-value-text-color:#18a058">
              <template #suffix>项</template>
            </NStatistic>
          </NCard>
        </NGi>
        <NGi>
          <NCard size="small" hoverable>
            <NStatistic label="提醒" :value="warnCnt" style="--n-value-text-color:#d48806">
              <template #suffix>项</template>
            </NStatistic>
          </NCard>
        </NGi>
        <NGi>
          <NCard size="small" hoverable>
            <NStatistic label="异常" :value="failCnt" style="--n-value-text-color:#d03050">
              <template #suffix>项</template>
            </NStatistic>
          </NCard>
        </NGi>
        <NGi>
          <NCard size="small" hoverable>
            <NStatistic label="执行错误" :value="errCnt" style="--n-value-text-color:#a05e03">
              <template #suffix>项</template>
            </NStatistic>
          </NCard>
        </NGi>
      </NGrid>

      <!-- 通过率 + 严重异常提醒 -->
      <NCard size="small" style="margin-bottom:12px">
        <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
          <span style="font-size:13px;color:#6b7280;white-space:nowrap">整体通过率</span>
          <div style="flex:1;min-width:240px">
            <NProgress
              type="line"
              :percentage="passRate"
              :height="14"
              :border-radius="7"
              :color="passRate >= 90 ? '#18a058' : passRate >= 70 ? '#d48806' : '#d03050'"
              indicator-placement="inside"
            >
              <span style="font-size:12px;font-weight:600;color:#fff">{{ passRate }}%</span>
            </NProgress>
          </div>
          <NTag v-if="criticalFail > 0" type="error" :bordered="false">
            {{ criticalFail }} 个严重（critical）异常待处置 —— 影响行情/分析
          </NTag>
          <NTag v-else type="success" :bordered="false">无严重异常</NTag>
        </div>
      </NCard>

      <!-- 明细 + 规则配置 + 对账记录 Tabs -->
      <NCard style="margin-bottom:12px">
        <NTabs type="line" animated>
          <NTabPane name="report" tab="规则最新结果">
            <NSpace style="margin-bottom:10px" :size="8">
              <NSelect v-model:value="tableFilter" :options="tableOptions" size="small" style="width:230px" clearable placeholder="按表过滤" />
              <NSelect v-model:value="groupFilter" :options="GROUP_OPTIONS" size="small" style="width:160px" data-testid="report-group-filter" />
              <NSelect
                v-model:value="statusFilter"
                size="small"
                style="width:130px"
                :options="[
                  { label: '全部结果', value: '' },
                  { label: '仅异常+提醒', value: 'warn_fail' },
                  { label: '通过', value: 'pass' },
                  { label: '提醒', value: 'warning' },
                  { label: '异常', value: 'fail' },
                ]"
              />
              <NInput v-model:value="ruleSearch" size="small" placeholder="搜索规则/表/说明" clearable style="width:200px" />
              <span style="color:#bbb;font-size:12px;line-height:28px">共 {{ filteredReport.length }} 条</span>
            </NSpace>
            <NDataTable
              :columns="reportColumns"
              :data="filteredReport"
              :loading="reportLoading"
              :row-key="(r: any) => r.id"
              size="small"
              :scroll-x="1150"
              :max-height="520"
            />
          </NTabPane>
          <NTabPane name="rules" :tab="`规则配置（${rulesItems.length} 条可启停）`">
            <NSpace style="margin-bottom:10px" :size="8">
              <NSelect v-model:value="ruleGroupFilter" :options="GROUP_OPTIONS" size="small" style="width:160px" data-testid="rules-group-filter" />
              <span style="color:#bbb;font-size:12px;line-height:28px">
                日检 31 条（每日 20:30）· 周检 5 条（周一 21:30，全史缺口/跨源衔接/覆盖率/OHLC 自洽/涨跌推导）
              </span>
            </NSpace>
            <NDataTable
              :columns="rulesColumns"
              :data="filteredRules"
              :loading="rulesLoading"
              :row-key="(r: any) => r.id"
              size="small"
              :scroll-x="1180"
              :max-height="560"
            />
          </NTabPane>
          <NTabPane name="recon" tab="对账记录">
            <NSpin :show="reconLoading" size="small">
              <!-- 轮次选择 + 差异汇总 -->
              <NSpace style="margin-bottom:12px" :size="8" align="center">
                <NSelect
                  v-model:value="reconRunAt"
                  :options="reconRoundOptions"
                  size="small"
                  style="width:420px"
                  placeholder="选择对账轮次"
                  data-testid="recon-round-select"
                />
                <NTag v-if="lagCount > 0" size="small" type="info" :bordered="false">
                  采集时延 {{ lagCount }} 条（远端比本地新 ≤5 天，非数据问题）
                </NTag>
                <NTag v-if="realDiffCount > 0" size="small" type="warning" :bordered="false">
                  真实差异 {{ realDiffCount }} 条
                </NTag>
                <NTag v-else-if="recon?.run_at" size="small" type="success" :bordered="false">本轮无真实差异</NTag>
              </NSpace>

              <!-- 轮次历史 -->
              <div v-if="(recon?.rounds || []).length" style="margin-bottom:14px">
                <div style="font-size:12.5px;font-weight:600;color:#1f2937;margin-bottom:6px">对账轮次（最近 10 次）</div>
                <div
                  v-for="r in recon.rounds"
                  :key="r.run_at + r.rule_name"
                  style="display:flex;gap:10px;align-items:baseline;font-size:12.5px;padding:4px 0;border-bottom:1px solid #f3f4f6"
                >
                  <span style="width:150px;color:#6b7280;flex-shrink:0">{{ fmtTime(r.run_at) }}</span>
                  <NTag size="tiny" :bordered="false" :type="r.status === 'pass' ? 'success' : 'warning'">
                    {{ r.status === 'pass' ? '无差异' : '有差异' }}
                  </NTag>
                  <span style="width:96px;color:#185FA5;flex-shrink:0">{{ MODE_CN[r.rule_name] || r.rule_name }}</span>
                  <span style="color:#6b7280;flex:1">{{ r.message }}</span>
                </div>
              </div>

              <!-- 差异明细 -->
              <div style="font-size:12.5px;font-weight:600;color:#1f2937;margin-bottom:6px">
                差异明细（{{ (recon?.details || []).length }} 条，最多展示 500）
              </div>
              <NDataTable
                :columns="reconDetailColumns"
                :data="recon?.details || []"
                :row-key="(r: any) => `${r.diff_type}-${r.stock_code}-${r.trade_date}-${r.col_name}`"
                size="small"
                :scroll-x="1100"
                :max-height="330"
              />

              <!-- 全史缺口待办（L1 检出 / L3 修复） -->
              <div style="font-size:12.5px;font-weight:600;color:#1f2937;margin:16px 0 6px">
                全史缺口待办（L1 检出 → L3 定向回补，任务 daily_backfill 手动触发）
              </div>
              <NSpace :size="8" style="margin-bottom:8px">
                <NTag size="small" :bordered="false">去重缺口 {{ recon?.gap_summary?.total ?? 0 }} 段</NTag>
                <NTag size="small" type="warning" :bordered="false">待修复 {{ recon?.gap_summary?.open_ ?? 0 }} 段</NTag>
                <NTag size="small" type="error" :bordered="false">高危（≥365 天）{{ recon?.gap_summary?.high_ ?? 0 }} 段</NTag>
                <NTag size="small" type="success" :bordered="false">已修复 {{ recon?.gap_summary?.fixed_ ?? 0 }} 段</NTag>
              </NSpace>
              <NDataTable
                :columns="gapColumns"
                :data="recon?.top_gaps || []"
                :row-key="(r: any) => r.stock_code"
                size="small"
                :scroll-x="600"
                :max-height="280"
              />
            </NSpin>
          </NTabPane>
        </NTabs>
      </NCard>

      <!-- 近 14 天趋势 -->
      <NCard size="small">
        <template #header>
          <NSpace align="center">
            <span>近 14 天体检趋势</span>
            <NTag size="tiny" :bordered="false" style="color:#bbb">每天各规则最终结果（同规则同日多轮取最后一轮，已排除对账）；堆叠 = 通过 / 提醒 / 异常 / 执行错误</NTag>
          </NSpace>
        </template>
        <div v-if="historyDays.length === 0" style="padding:20px 0">
          <NEmpty description="暂无趋势数据（体检满 2 天后可见）" style="padding:10px 0" />
        </div>
        <div v-else style="display:flex;align-items:flex-end;gap:10px;height:120px;padding:4px 2px 0">
          <div v-for="d in historyDays" :key="d.d" style="flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0">
            <div style="width:100%;display:flex;flex-direction:column-reverse;height:88px;background:#f3f4f6;border-radius:4px;overflow:hidden">
              <div :style="{ height: (d.pass / barMax) * 100 + '%', background: '#18a058' }" :title="`通过 ${d.pass}`" />
              <div :style="{ height: (d.warning / barMax) * 100 + '%', background: '#d48806' }" :title="`提醒 ${d.warning}`" />
              <div :style="{ height: (d.fail / barMax) * 100 + '%', background: '#d03050' }" :title="`异常 ${d.fail}`" />
              <div :style="{ height: (d.error / barMax) * 100 + '%', background: '#a05e03' }" :title="`执行错误 ${d.error}`" />
            </div>
            <span style="font-size:11px;color:#9ca3af;white-space:nowrap">{{ d.d }}</span>
          </div>
        </div>
      </NCard>
    </template>
  </div>
</template>
