<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { NCard, NSpace, NSelect, NDataTable, NInput, type DataTableColumns } from 'naive-ui'
import KLineChart from '../components/KLineChart.vue'
import IndexDetailDrawer from '../components/IndexDetailDrawer.vue'
import api from '../api'

// ---------- 标的选择（股票 / 指数 二选一，共同驱动下方 K 线） ----------
const selType = ref<'stock' | 'index'>('stock')
const currentCode = ref('000001')
const currentName = ref('平安银行')

// ---------- 股票搜索 ----------
const stockOptions = ref<{ label: string; value: string }[]>([])

async function searchStocks(keyword: string) {
  if (!keyword) {
    stockOptions.value = []
    return
  }
  try {
    const resp: any = await api.get('/market/stock-search', { params: { keyword, limit: 20 } })
    stockOptions.value = (resp.items || []).map((s: any) => ({
      label: `${s.stock_code} ${s.short_name}`,
      value: s.stock_code,
    }))
  } catch (e) {
    console.error('[search]', e)
    stockOptions.value = []
  }
}

function onSelect(code: string) {
  if (!code) return
  const hit = stockOptions.value.find((o) => o.value === code)
  selType.value = 'stock'
  currentCode.value = code
  currentName.value = hit ? hit.label.replace(`${code} `, '') : code
}

// ---------- 大盘指数行情 ----------
interface IndexRow {
  index_code: string
  index_name: string
  trade_date: string
  close: number
  change_pct: number
  change_amount: number
  amount: number
  ytd_change_pct: number | null
  data_source: string
}

const indices = ref<IndexRow[]>([])
const indexLoading = ref(false)
const indexDate = ref('')
const indexError = ref('')

async function loadIndices() {
  indexLoading.value = true
  indexError.value = ''
  try {
    const resp: any = await api.get('/market/index-list')
    indices.value = resp.items || []
    if (indices.value.length) indexDate.value = indices.value[0].trade_date
    if (!indices.value.length) indexError.value = '暂无指数行情数据'
  } catch (e: any) {
    console.error('[index-list]', e)
    indexError.value = '指数行情加载失败'
  } finally {
    indexLoading.value = false
  }
}

function pickIndex(it: IndexRow) {
  selType.value = 'index'
  currentCode.value = it.index_code
  currentName.value = it.index_name
}

// ---------- 指数详情抽屉（释义/成分股/行业分布） ----------
const drawerShow = ref(false)
const drawerCode = ref('')
const drawerName = ref('')

function openCons(it: IndexRow) {
  drawerCode.value = it.index_code
  drawerName.value = it.index_name
  drawerShow.value = true
}

function tint(v: number | null | undefined): string {
  if (v == null) return 'flat'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '--'
  return `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (v == null) return '--'
  return `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}`
}

function fmtAmount(v: number | null | undefined): string {
  if (v == null) return '--'
  const yi = v / 1e8
  if (yi >= 1e4) return `${(yi / 1e4).toFixed(2)}万亿`
  return `${yi.toFixed(yi >= 100 ? 0 : 1)}亿`
}

// ---------- 行情表格 ----------
interface Row {
  stock_code: string
  stock_name: string
  new: number
  change_pct: number
  change_amount: number
  amount: number
  turnover_ratio: number
  dynamic_pe: number
  ytd_change_pct: number
}

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const sortField = ref('change_pct')
const sortOrder = ref('desc')
const keyword = ref('')
const loading = ref(false)

const pctRender = (v: number) =>
  v == null
    ? '--'
    : h(
        'span',
        { class: v > 0 ? 'c-up' : v < 0 ? 'c-down' : '' },
        `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`,
      )
const numRender = (v: number, digits = 2) => (v == null ? '--' : Number(v).toFixed(digits))

const columns: DataTableColumns<Row> = [
  { title: '代码', key: 'stock_code', width: 90, fixed: 'left' },
  {
    title: '名称',
    key: 'stock_name',
    width: 110,
    fixed: 'left',
    render: (r) =>
      h(
        'a',
        {
          class: 'stock-link',
          onClick: () => {
            selType.value = 'stock'
            currentCode.value = r.stock_code
            currentName.value = r.stock_name
          },
        },
        r.stock_name,
      ),
  },
  { title: '最新价', key: 'new', width: 90, render: (r) => numRender(r.new) },
  { title: '涨跌幅', key: 'change_pct', width: 95, sorter: true, render: (r) => pctRender(r.change_pct) },
  { title: '涨跌额', key: 'change_amount', width: 90, render: (r) => numRender(r.change_amount) },
  { title: '今开', key: 'open', width: 85, render: (r) => numRender((r as any).open) },
  { title: '最高', key: 'high', width: 85, render: (r) => numRender((r as any).high) },
  { title: '最低', key: 'low', width: 85, render: (r) => numRender((r as any).low) },
  { title: '成交量(手)', key: 'volume', width: 100, render: (r) => formatVol((r as any).volume) },
  { title: '成交额(亿)', key: 'amount', width: 100, sorter: true, render: (r) => numRender(r.amount / 1e8, 2) },
  { title: '换手率', key: 'turnover_ratio', width: 90, render: (r) => `${numRender(r.turnover_ratio, 2)}%` },
  { title: '市盈率', key: 'dynamic_pe', width: 90, render: (r) => numRender(r.dynamic_pe, 2) },
  { title: '市净率', key: 'pb', width: 90, render: (r) => numRender((r as any).pb, 2) },
  { title: '年初至今', key: 'ytd_change_pct', width: 100, sorter: true, render: (r) => pctRender(r.ytd_change_pct) },
]

function formatVol(v?: number): string {
  if (!v && v !== 0) return '--'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return String(v)
}

async function loadRows() {
  loading.value = true
  try {
    const resp: any = await api.get('/market/current', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        sort: sortField.value,
        order: sortOrder.value,
        keyword: keyword.value,
      },
    })
    rows.value = resp.items || []
    total.value = resp.total || 0
  } catch (e) {
    console.error('[current]', e)
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadRows()
}
function onPageSizeChange(ps: number) {
  pageSize.value = ps
  page.value = 1
  loadRows()
}
function onSortChange(sorter: any) {
  if (!sorter || !sorter.columnKey) return
  sortField.value = sorter.columnKey
  sortOrder.value = sorter.order === 'ascend' ? 'asc' : 'desc'
  loadRows()
}

onMounted(() => {
  loadIndices()
  loadRows()
})
</script>

<template>
  <div>
    <!-- 大盘指数：行情条，点击切换下方 K 线 -->
    <NCard style="margin-bottom: 12px">
      <template #header>
        <NSpace align="center" justify="space-between" style="width: 100%">
          <span>大盘指数</span>
          <span class="idx-meta">
            <template v-if="indexDate">最新交易日 {{ indexDate }} · </template>
            数据源：中证指数官网 / 国证指数（含成交额）
          </span>
        </NSpace>
      </template>

      <div v-if="indexLoading" class="idx-empty">加载中…</div>
      <div v-else-if="indexError" class="idx-empty">{{ indexError }}</div>
      <div v-else class="idx-grid">
        <div
          v-for="it in indices"
          :key="it.index_code"
          class="idx-card"
          :class="{ active: selType === 'index' && currentCode === it.index_code }"
          @click="pickIndex(it)"
        >
          <div class="idx-head">
            <span class="idx-name" :title="it.index_name">{{ it.index_name }}</span>
            <span class="idx-code">{{ it.index_code }}</span>
          </div>
          <div class="idx-close" :class="tint(it.change_pct)">
            {{ it.close != null ? Number(it.close).toFixed(2) : '--' }}
          </div>
          <div class="idx-pct" :class="tint(it.change_pct)">
            <span>{{ fmtPct(it.change_pct) }}</span>
            <span class="idx-chg">{{ fmtSigned(it.change_amount) }}</span>
          </div>
          <div class="idx-foot">
            <span>额 {{ fmtAmount(it.amount) }}</span>
            <span :class="tint(it.ytd_change_pct)">年 {{ fmtPct(it.ytd_change_pct) }}</span>
            <span
              class="idx-cons-btn"
              title="指数释义 / 成分股 / 行业分布"
              @click.stop="openCons(it)"
            >成分</span>
          </div>
        </div>
      </div>
    </NCard>

    <!-- 顶部：搜索 + 当前标的 -->
    <NCard style="margin-bottom: 12px">
      <NSpace align="center" :size="16" wrap>
        <NSelect
          v-model:value="currentCode"
          :options="stockOptions"
          filterable
          clearable
          placeholder="输入代码/名称搜索股票"
          style="width: 320px"
          :on-update:value="onSelect"
          @search="searchStocks"
        />
        <span style="font-size: 15px; font-weight: 600; color: #333">
          当前标的：{{ currentName }}（{{ currentCode }}）
          <span v-if="selType === 'index'" class="tag-idx">指数</span>
        </span>
      </NSpace>
    </NCard>

    <!-- K 线（股票 / 指数共用） -->
    <NCard style="margin-bottom: 12px">
      <KLineChart
        :code="currentCode"
        :name="currentName"
        :is-index="selType === 'index'"
        :limit="250"
      />
    </NCard>

    <!-- 指数详情抽屉：释义 / 成分股 / 行业分布 -->
    <IndexDetailDrawer
      v-model:show="drawerShow"
      :index-code="drawerCode"
      :index-name="drawerName"
    />

    <!-- 最新行情表格 -->
    <NCard>
      <template #header>
        <NSpace align="center" justify="space-between" style="width: 100%">
          <span>沪深 A 股最新行情</span>
          <NInput
            v-model:value="keyword"
            placeholder="搜索代码/名称"
            clearable
            style="width: 200px"
            @keyup.enter="page = 1; loadRows()"
            @clear="page = 1; loadRows()"
          />
        </NSpace>
      </template>
      <NDataTable
        :columns="columns"
        :data="rows"
        :loading="loading"
        :row-key="(r: any) => r.stock_code"
        :pagination="{
          page: page,
          pageSize: pageSize,
          itemCount: total,
          pageSizes: [20, 50, 100],
          showSizePicker: true,
          onChange: onPageChange,
          onUpdatePageSize: onPageSizeChange,
        }"
        :remote="true"
        @update:sorter="onSortChange"
        size="small"
        :scroll-x="1200"
      />
    </NCard>
  </div>
</template>

<style scoped>
.stock-link {
  color: #1e6fff;
  cursor: pointer;
  text-decoration: none;
}
.stock-link:hover {
  text-decoration: underline;
}
.c-up {
  color: #ef232a;
}
.c-down {
  color: #14b143;
}

/* ---------- 大盘指数行情条 ---------- */
.idx-meta {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-flat, #909399);
}
.idx-empty {
  padding: 18px;
  text-align: center;
  font-size: 13px;
  color: var(--color-flat, #909399);
}
.idx-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(178px, 1fr));
  gap: 10px;
}
.idx-card {
  border: 1px solid #e6e8ec;
  border-radius: 6px;
  padding: 8px 10px 9px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.idx-card:hover {
  border-color: #b9d3ee;
  background: #f7fbff;
}
.idx-card.active {
  border-color: var(--color-primary, #185fa5);
  background: #e6f1fb;
  box-shadow: inset 0 0 0 1px var(--color-primary, #185fa5);
}
.idx-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}
.idx-name {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.idx-card.active .idx-name {
  color: var(--color-primary, #185fa5);
}
.idx-code {
  font-size: 10px;
  color: #9ca3af;
  font-family: Consolas, Menlo, monospace;
  flex: none;
}
.idx-close {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.35;
  font-variant-numeric: tabular-nums;
}
.idx-pct {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.idx-chg {
  font-size: 11px;
  opacity: 0.85;
}
.idx-foot {
  margin-top: 5px;
  padding-top: 5px;
  border-top: 1px dashed #eef1f5;
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-size: 11px;
  color: var(--color-flat, #909399);
}
.idx-cons-btn {
  flex: none;
  color: var(--color-primary, #185fa5);
  cursor: pointer;
  padding: 0 4px;
  border-radius: 3px;
}
.idx-cons-btn:hover {
  background: #e6f1fb;
  text-decoration: underline;
}
.up {
  color: var(--color-up, #ef232a);
}
.down {
  color: var(--color-down, #14b143);
}
.flat {
  color: var(--color-flat, #909399);
}
.tag-idx {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-primary, #185fa5);
  background: #e6f1fb;
  vertical-align: 1px;
}
</style>
