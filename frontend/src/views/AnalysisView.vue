<script setup lang="ts">
import { ref, onMounted, h, computed } from 'vue'
import { NCard, NTab, NTabs, NDataTable, NSelect, type DataTableColumns } from 'naive-ui'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, TitleComponent } from 'echarts/components'
import api from '../api'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent, TitleComponent])

const activeTab = ref('dividend')

const pctRender = (v: any) =>
  v == null || v === '--'
    ? '--'
    : h('span', { class: Number(v) > 0 ? 'c-up' : Number(v) < 0 ? 'c-down' : '' }, `${Number(v) > 0 ? '+' : ''}${Number(v)}%`)

// ================= 股息率排行 =================
const divRows = ref<any[]>([])
const divLoading = ref(false)
async function loadDividend() {
  divLoading.value = true
  try {
    const resp: any = await api.get('/analysis/dividend-rank', { params: { limit: 50 } })
    divRows.value = resp.items || []
  } catch (e) {
    console.error('[dividend]', e)
  } finally {
    divLoading.value = false
  }
}

const divColumns: DataTableColumns<any> = [
  { title: '排名', key: 'idx', width: 60, render: (_, i) => i + 1 },
  { title: '代码', key: 'stock_code', width: 90 },
  { title: '名称', key: 'stock_name', width: 110 },
  { title: '报告期', key: 'report_period', width: 110 },
  { title: '税前股息率', key: 'pre_tax_dividend_ratio', width: 110, render: (r) => (r.pre_tax_dividend_ratio == null ? '--' : `${r.pre_tax_dividend_ratio}%`) },
  { title: '现价', key: 'new', width: 90, render: (r) => (r.new == null ? '--' : Number(r.new).toFixed(2)) },
  { title: '涨跌幅', key: 'change_pct', width: 95, render: (r) => pctRender(r.change_pct) },
  { title: '分红方案', key: 'dividend_plan_desc', ellipsis: { tooltip: true } },
]

// ================= 概念排名 =================
const period = ref(1)
const periodOptions = [
  { label: '今日涨幅', value: 1 },
  { label: '5日涨幅', value: 5 },
  { label: '10日涨幅', value: 10 },
  { label: '20日涨幅', value: 20 },
]
const conceptRows = ref<any[]>([])
const conceptLoading = ref(false)
async function loadConceptRank() {
  conceptLoading.value = true
  try {
    const resp: any = await api.get('/analysis/concept-rank', { params: { period: period.value, limit: 50 } })
    conceptRows.value = resp.items || []
  } catch (e) {
    console.error('[concept-rank]', e)
  } finally {
    conceptLoading.value = false
  }
}

const conceptColumns: DataTableColumns<any> = [
  { title: '排名', key: 'idx', width: 60, render: (_, i) => i + 1 },
  { title: '概念名称', key: 'concept_name', width: 140 },
  { title: '区间涨跌幅', key: 'change_pct', width: 110, render: (r) => pctRender(r.change_pct) },
  { title: '最新指数', key: 'close', width: 100, render: (r) => (r.close == null ? '--' : Number(r.close).toFixed(2)) },
  { title: '成交额(亿)', key: 'amount', width: 110, render: (r) => (r.amount == null ? '--' : (r.amount / 1e8).toFixed(2)) },
]

// ================= YTD 排行 =================
const ytdRows = ref<any[]>([])
const ytdLoading = ref(false)
const ytdOrder = ref<'desc' | 'asc'>('desc')
const ytdOrderLabel = computed(() => (ytdOrder.value === 'desc' ? '涨幅 TOP' : '跌幅 TOP'))
async function loadYtd() {
  ytdLoading.value = true
  try {
    const resp: any = await api.get('/analysis/ytd-rank', { params: { limit: 50, order: ytdOrder.value } })
    ytdRows.value = resp.items || []
  } catch (e) {
    console.error('[ytd]', e)
  } finally {
    ytdLoading.value = false
  }
}

const ytdColumns: DataTableColumns<any> = [
  { title: '排名', key: 'idx', width: 60, render: (_, i) => i + 1 },
  { title: '代码', key: 'stock_code', width: 90 },
  { title: '名称', key: 'stock_name', width: 110 },
  { title: '年初至今涨幅', key: 'ytd_change_pct', width: 120, render: (r) => pctRender(r.ytd_change_pct) },
  { title: '最新价', key: 'new', width: 90, render: (r) => (r.new == null ? '--' : Number(r.new).toFixed(2)) },
  { title: '年初价', key: 'year_start', width: 90, render: (r) => (r.year_start == null ? '--' : Number(r.year_start).toFixed(2)) },
]

// ================= 图表 =================
const chartData = computed(() => {
  if (activeTab.value === 'dividend') {
    const top = divRows.value.slice(0, 15)
    return {
      title: '股息率 TOP 15',
      names: top.map((r) => r.stock_name),
      values: top.map((r) => (r.pre_tax_dividend_ratio == null ? 0 : Number(r.pre_tax_dividend_ratio))),
      color: '#185FA5',
      suffix: '%',
    }
  }
  if (activeTab.value === 'concept') {
    const top = conceptRows.value.slice(0, 15)
    return {
      title: `概念 ${period.value === 1 ? '今日' : `${period.value}日`}涨幅 TOP 15`,
      names: top.map((r) => r.concept_name),
      values: top.map((r) => Number(r.change_pct ?? 0)),
      color: '#EF232A',
      suffix: '%',
    }
  }
  const top = ytdRows.value.slice(0, 15)
  return {
    title: `YTD ${ytdOrderLabel.value} 15`,
    names: top.map((r) => r.stock_name),
    values: top.map((r) => Number(r.ytd_change_pct ?? 0)),
    color: ytdOrder.value === 'desc' ? '#EF232A' : '#14B143',
    suffix: '%',
  }
})

const chartOption = computed(() => ({
  title: { text: chartData.value.title, left: 'center', textStyle: { fontSize: 14 } },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (params: any[]) => {
      const p = params[0]
      return `${p.name}：${p.value}${chartData.value.suffix}`
    },
  },
  grid: { left: 80, right: 30, top: 40, bottom: 30 },
  xAxis: { type: 'value', axisLabel: { formatter: `{value}${chartData.value.suffix}` } },
  yAxis: {
    type: 'category',
    data: chartData.value.names,
    axisLabel: { fontSize: 11, width: 100, overflow: 'truncate' },
  },
  series: [
    {
      type: 'bar',
      data: chartData.value.values,
      itemStyle: { color: chartData.value.color, borderRadius: [0, 3, 3, 0] },
      barMaxWidth: 18,
      label: { show: true, position: 'right', formatter: `{c}${chartData.value.suffix}`, fontSize: 10 },
    },
  ],
}))

// ================= 事件 =================
function onTabChange(key: string) {
  activeTab.value = key
  if (key === 'dividend' && !divRows.value.length) loadDividend()
  if (key === 'concept' && !conceptRows.value.length) loadConceptRank()
  if (key === 'ytd' && !ytdRows.value.length) loadYtd()
}

onMounted(() => loadDividend())
</script>

<template>
  <div>
    <NCard hoverable>
      <NTabs v-model:value="activeTab" type="line" animated @update:value="onTabChange">
        <!-- 股息率 -->
        <NTab name="dividend" tab="股息率排行">
          <NSpace vertical :size="12">
            <NCard size="small" title="股息率排行（最近年报税前股息率）" :bordered="false">
              <NDataTable
                :columns="divColumns"
                :data="divRows"
                :loading="divLoading"
                :row-key="(r: any) => r.stock_code"
                size="small"
                :scroll-x="900"
                :max-height="520"
              />
            </NCard>
            <NCard size="small" title="TOP 15 可视化" :bordered="false">
              <VChart :option="chartOption" style="height: 420px" autoresize />
            </NCard>
          </NSpace>
        </NTab>

        <!-- 概念排名 -->
        <NTab name="concept" tab="概念板块排名">
          <NSpace vertical :size="12">
            <NCard size="small" title="概念板块区间涨幅排名" :bordered="false">
              <template #header-extra>
                <NSelect
                  v-model:value="period"
                  :options="periodOptions"
                  size="small"
                  style="width: 120px"
                  @update:value="loadConceptRank"
                />
              </template>
              <NDataTable
                :columns="conceptColumns"
                :data="conceptRows"
                :loading="conceptLoading"
                :row-key="(r: any) => r.index_code"
                size="small"
                :scroll-x="700"
                :max-height="520"
              />
            </NCard>
            <NCard size="small" title="TOP 15 可视化" :bordered="false">
              <VChart :option="chartOption" style="height: 420px" autoresize />
            </NCard>
          </NSpace>
        </NTab>

        <!-- YTD -->
        <NTab name="ytd" tab="年初至今涨幅">
          <NSpace vertical :size="12">
            <NCard size="small" title="年初至今涨幅排行" :bordered="false">
              <template #header-extra>
                <span class="rank-switch" style="cursor: pointer" @click="ytdOrder = ytdOrder === 'desc' ? 'asc' : 'desc'; loadYtd()">
                  点击切换：<span :class="ytdOrder === 'desc' ? 'c-up' : 'c-down'">{{ ytdOrderLabel }}</span>
                </span>
              </template>
              <NDataTable
                :columns="ytdColumns"
                :data="ytdRows"
                :loading="ytdLoading"
                :row-key="(r: any) => r.stock_code"
                size="small"
                :scroll-x="700"
                :max-height="520"
              />
            </NCard>
            <NCard size="small" title="TOP 15 可视化" :bordered="false">
              <VChart :option="chartOption" style="height: 420px" autoresize />
            </NCard>
          </NSpace>
        </NTab>
      </NTabs>
    </NCard>
  </div>
</template>

<style scoped>
.rank-switch {
  font-size: 12px;
  color: #6b7280;
  background: #f5f7fa;
  padding: 2px 10px;
  border-radius: 4px;
  user-select: none;
  transition: background 0.15s;
}
.rank-switch:hover {
  background: #e6f1fb;
}
.c-up {
  color: var(--color-up, #ef232a);
  font-weight: 600;
}
.c-down {
  color: var(--color-down, #14b143);
  font-weight: 600;
}
</style>
