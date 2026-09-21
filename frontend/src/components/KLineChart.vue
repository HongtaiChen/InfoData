<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { init, dispose } from 'klinecharts'
import type { Chart, KLineData } from 'klinecharts'
import dayjs from 'dayjs'
import api from '../api'

const props = defineProps<{
  code: string
  name?: string
  isConcept?: boolean
  /** 大盘指数 K 线（dc_index_market） */
  isIndex?: boolean
  limit?: number
}>()

const containerRef = ref<HTMLDivElement>()
let chart: Chart | null = null

const loadingMsg = ref('加载中...')
const latest = ref<any>(null)
// 最近一根的**真实涨跌幅**，来自接口 `change_pct`（按 hfq 派生、含除权调整，与行情表同口径）。
// ⚠️ 不要用相邻两根 close 现算（2026-09-20 口径改造后修正）：主表已改存**不复权实际价**，
// 除权日现算会把「除权跳空」当成跌幅，与同一组件十字光标的涨跌幅（用的是接口值）自相矛盾。
let latestApiPct: number | null = null

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
          backgroundColor: '#EF232A',
          color: '#ffffff',
          borderRadius: 2,
        },
      },
      vertical: {
        line: { color: '#b8b8b8', style: 'dashed' },
        text: {
          backgroundColor: '#EF232A',
          color: '#ffffff',
          borderRadius: 2,
        },
      },
    },
    candle: {
      bar: {
        upColor: '#EF232A',
        downColor: '#14B143',
        noChangeColor: '#999999',
        upBorderColor: '#EF232A',
        downBorderColor: '#14B143',
        upWickColor: '#EF232A',
        downWickColor: '#14B143',
      },
      priceMark: {
        high: { color: '#EF232A' },
        low: { color: '#14B143' },
        last: {
          upColor: '#EF232A',
          downColor: '#14B143',
          noChangeColor: '#999999',
        },
      },
      tooltip: {
        legend: {
          // v10：OHLC 十字光标信息栏由 legend.template 回调产出（替代旧 custom）
          template: (data: any) => {
            const d = data.current
            if (!d) return []
            return [
              { title: '时间', value: dayjs(d.timestamp).format('YYYY-MM-DD') },
              { title: '开盘', value: d.open.toFixed(2) },
              { title: '最高', value: d.high.toFixed(2) },
              { title: '最低', value: d.low.toFixed(2) },
              { title: '收盘', value: d.close.toFixed(2) },
              { title: '涨跌幅', value: `${d.changePct ?? ''}` },
              { title: '成交量', value: formatVol(d.volume) },
            ]
          },
        },
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

async function fetchBars(): Promise<KLineData[]> {
  const resp: any = await api.get('/market/kline', {
    params: {
      code: props.code,
      limit: props.limit ?? 250,
      is_concept: props.isConcept ?? false,
      is_index: props.isIndex ?? false,
    },
  })
  const items = resp.items || []
  const lastRaw = items[items.length - 1]
  latestApiPct = lastRaw && lastRaw.change_pct != null ? Number(lastRaw.change_pct) : null
  return items.map((it: any) => {
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
}

function setLatest(list: KLineData[]) {
  if (list.length === 0) {
    loadingMsg.value = '暂无K线数据'
    return
  }
  const last = list[list.length - 1]
  latest.value = {
    trade_date: dayjs(last.timestamp).format('YYYY-MM-DD'),
    close: last.close,
    changePct: latestApiPct != null
      ? latestApiPct
      : (list.length > 1
        ? ((last.close - list[list.length - 2].close) / list[list.length - 2].close) * 100
        : null),
  }
}

function initChart() {
  if (!containerRef.value) return
  if (chart) {
    dispose(chart)
    chart = null
  }
  chart = init(containerRef.value)
  if (!chart) return
  loadingMsg.value = '加载中...'
  applyTonghuashunStyle(chart)
  // 主图 MA + 副图 VOL/MACD（同花顺经典布局）
  chart.createIndicator('MA', false)
  chart.createIndicator('VOL')
  chart.createIndicator('MACD')
  // klinecharts >=9.x 使用 DataLoader（替代旧的 applyNewData）
  chart.setDataLoader({
    getBars: ({ type, callback }) => {
      if (type !== 'init' && type !== 'backward' && type !== 'forward') return
      fetchBars()
        .then((list) => {
          setLatest(list)
          loadingMsg.value = ''
          callback(list, false)
        })
        .catch((e) => {
          loadingMsg.value = `加载失败: ${e?.message || e}`
          console.error('[KLine]', props.code, e)
          callback([], false)
        })
    },
  })
  // klinecharts v10 关键：init() 不预设 symbol/period，二者为 null 时
  // _processDataLoad 会直接 return，getBars 永不触发（K 线一直停在「加载中」）。
  // 必须先 setDataLoader 再 setSymbol + setPeriod 才会真正发起首屏拉数。
  chart.setSymbol({ ticker: props.code })
  chart.setPeriod({ span: 1, type: 'day' })
  // 布局尺寸兜底：等一帧确保容器已按 CSS 定尺后再重算一次
  requestAnimationFrame(() => {
    if (chart) chart.resize()
  })
}

onMounted(() => {
  initChart()
})

onBeforeUnmount(() => {
  if (chart) {
    dispose(chart)
    chart = null
  }
})

watch(
  () => [props.code, props.isIndex, props.isConcept],
  () => {
    // 切换标的（或跨类型切换）：重建 chart 触发 DataLoader.init 重新拉数
    latest.value = null
    initChart()
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
/* klinecharts 要求容器有「确定高度」：height:100% 挂在 auto 高度父级上会算成 0，
   导致内部 pane 高度全为 0（只剩 X 轴），故这里用固定像素高度 */
.kline-container {
  width: 100%;
  height: 420px;
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
  color: #EF232A;
}
.down {
  color: #14B143;
}
</style>
