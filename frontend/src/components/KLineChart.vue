<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { init, dispose } from 'klinecharts'
import type { KLineChart as Chart, KLineData } from 'klinecharts'
import dayjs from 'dayjs'
import api from '../api'

const props = defineProps<{
  code: string
  name?: string
  isConcept?: boolean
  limit?: number
}>()

const containerRef = ref<HTMLDivElement>()
let chart: Chart | null = null
let loadingFlag = false

const loadingMsg = ref('加载中...')
const latest = ref<any>(null)

// 同花顺风格样式（白底 + 红涨绿跌 + 十字光标 OHLC 信息栏）
function applyTonghuashunStyle(c: Chart) {
  c.setStyles({
    grid: {
      horizontal: { color: '#eceff3', size: 1 },
      vertical: { color: '#eceff3', size: 1 },
    },
    crosshair: {
      horizontal: {
        line: { color: '#b8b8b8', style: 'dashed' },
        text: {
          backgroundColor: '#e64a4a',
          color: '#ffffff',
          borderRadius: 2,
        },
      },
      vertical: {
        line: { color: '#b8b8b8', style: 'dashed' },
        text: {
          backgroundColor: '#e64a4a',
          color: '#ffffff',
          borderRadius: 2,
        },
      },
    },
    candle: {
      bar: {
        upColor: '#e64a4a',
        downColor: '#17a05e',
        noChangeColor: '#999999',
        upBorderColor: '#e64a4a',
        downBorderColor: '#17a05e',
        upWickColor: '#e64a4a',
        downWickColor: '#17a05e',
      },
      priceMark: {
        high: { color: '#e64a4a' },
        low: { color: '#17a05e' },
        last: {
          upColor: '#e64a4a',
          downColor: '#17a05e',
          noChangeColor: '#999999',
        },
      },
      tooltip: {
        custom: [
          { title: '时间：', value: (d: any) => dayjs(d.timestamp).format('YYYY-MM-DD') },
          { title: '开盘：', value: (d: any) => d.open.toFixed(2) },
          { title: '最高：', value: (d: any) => d.high.toFixed(2) },
          { title: '最低：', value: (d: any) => d.low.toFixed(2) },
          { title: '收盘：', value: (d: any) => d.close.toFixed(2) },
          { title: '涨跌幅：', value: (d: any) => `${d.changePct ?? ''}` },
          { title: '成交量：', value: (d: any) => formatVol(d.volume) },
        ],
      },
    },
    indicator: {
      tooltip: {
        custom: [
          { title: '时间：', value: (d: any) => dayjs(d.timestamp).format('YYYY-MM-DD') },
        ],
      },
    },
    xAxis: {
      axisLine: { color: '#d4d7de' },
      tickText: { color: '#666666' },
    },
    yAxis: {
      axisLine: { color: '#d4d7de' },
      tickText: { color: '#666666' },
    },
    separator: { color: '#e6e8ec' },
  })
}

function formatVol(v?: number): string {
  if (!v && v !== 0) return '-'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return String(v)
}

async function loadKline() {
  if (!props.code || loadingFlag) return
  loadingFlag = true
  loadingMsg.value = '加载中...'
  try {
    const resp: any = await api.get('/market/kline', {
      params: { code: props.code, limit: props.limit ?? 250, is_concept: props.isConcept ?? false },
    })
    const list: KLineData[] = (resp.items || []).map((it: any) => {
      const pct = it.change_pct != null ? Number(it.change_pct) : null
      return {
        timestamp: dayjs(it.trade_date).valueOf(),
        open: Number(it.open),
        high: Number(it.high),
        low: Number(it.low),
        close: Number(it.close),
        volume: Number(it.volume),
        changePct: pct != null ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '',
      }
    })
    if (!chart) return
    if (list.length === 0) {
      loadingMsg.value = '暂无K线数据'
      chart.applyNewData([])
      return
    }
    chart.applyNewData(list)
    const last = list[list.length - 1]
    latest.value = {
      trade_date: dayjs(last.timestamp).format('YYYY-MM-DD'),
      close: last.close,
      changePct: list.length > 1
        ? ((last.close - list[list.length - 2].close) / list[list.length - 2].close) * 100
        : null,
    }
    loadingMsg.value = ''
  } catch (e: any) {
    loadingMsg.value = `加载失败: ${e?.message || e}`
    console.error('[KLine]', props.code, e)
  } finally {
    loadingFlag = false
  }
}

onMounted(() => {
  if (!containerRef.value) return
  chart = init(containerRef.value)
  if (chart) {
    applyTonghuashunStyle(chart)
    // 主图 MA + 副图 VOL/MACD（同花顺经典布局）
    chart.createIndicator('MA', false, { id: 'candle_pane' })
    chart.createIndicator('VOL')
    chart.createIndicator('MACD')
    loadKline()
  }
})

onBeforeUnmount(() => {
  if (chart) {
    dispose(chart)
    chart = null
  }
})

watch(
  () => props.code,
  () => {
    if (chart) loadKline()
  },
)
</script>

<template>
  <div class="kline-wrap">
    <div v-if="loadingMsg" class="kline-loading">{{ loadingMsg }}</div>
    <div v-if="latest && !loadingMsg" class="kline-head">
      <span class="k-name">{{ name || props.code }}</span>
      <span class="k-date">{{ latest.trade_date }}</span>
      <span class="k-close" :class="(latest.changePct ?? 0) >= 0 ? 'up' : 'down'">
        收 {{ latest.close }}
      </span>
      <span class="k-pct" :class="(latest.changePct ?? 0) >= 0 ? 'up' : 'down'">
        {{ latest.changePct != null ? `${latest.changePct >= 0 ? '+' : ''}${latest.changePct.toFixed(2)}%` : '' }}
      </span>
    </div>
    <div ref="containerRef" class="kline-container" />
  </div>
</template>

<style scoped>
.kline-wrap {
  position: relative;
  width: 100%;
  height: 100%;
}
.kline-container {
  width: 100%;
  height: 100%;
  min-height: 400px;
}
.kline-loading {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: #999;
  font-size: 13px;
  z-index: 2;
  background: rgba(255, 255, 255, 0.85);
  padding: 8px 16px;
  border-radius: 4px;
}
.kline-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 0 4px 6px;
  font-size: 13px;
}
.k-name {
  font-size: 15px;
  font-weight: 600;
}
.k-date {
  color: #888;
}
.k-close {
  font-weight: 600;
}
.up {
  color: #e64a4a;
}
.down {
  color: #17a05e;
}
</style>
