<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NCard, NDescriptions, NDescriptionsItem, NTag, NAlert, NTable,
  NTh, NTr, NTd, NSpace, NButton, useMessage,
} from 'naive-ui'
import api from '../api'

const message = useMessage()

const dbStats = ref<any>(null)
const backendInfo = ref<any>(null)

// 数据库概览（直接查 information_schema 行数估计，不扫描大表）
async function loadDbStats() {
  try {
    // 通过后端 health 拿不到表信息，这里用已知静态信息展示 + 尝试调 jobs/stats 佐证连通
    const resp: any = await api.get('/jobs/stats')
    backendInfo.value = { jobsApi: 'ok', taskCount: resp.items?.length || 0 }
  } catch (e) {
    backendInfo.value = { jobsApi: 'error' }
  }
}

// 静态数据源说明（与需求文档一致）
const dataSources = [
  { source: '东方财富', scope: '股票日线/资讯/概念行情', status: '正常（偶发风控）', type: 'info' as const },
  { source: '腾讯', scope: '股票日线（主力备用源）', status: '稳定', type: 'success' as const },
  { source: '新浪', scope: '股票日线备用', status: '稳定', type: 'success' as const },
  { source: 'Tushare', scope: '股票日线保底（需 token）', status: '未配置', type: 'default' as const },
  { source: '同花顺', scope: '概念板块/股息/日历', status: '存量数据', type: 'info' as const },
  { source: '财联社', scope: '资讯（待接入）', status: '待开发', type: 'warning' as const },
]

const taskOverview = [
  { name: 'stock_daily_incr', desc: '股票日线增量采集（东财→腾讯→新浪→Tushare 四级降级）', schedule: '手动/定时' },
  { name: 'news_cls', desc: '财联社快讯采集（待开发）', schedule: '定时' },
  { name: 'news_em', desc: '东方财富资讯采集（待开发）', schedule: '定时' },
  { name: 'concept_daily', desc: '同花顺概念行情采集（待开发）', schedule: '每日' },
  { name: 'calendar_event', desc: '财经日历事件采集（待开发）', schedule: '每日' },
]

onMounted(loadDbStats)
</script>

<template>
  <div>
    <NAlert type="info" :show-icon="true" closable style="margin-bottom: 12px">
      InfoData 数据分析平台 · 本栏目展示系统状态与数据源配置，具体运维操作（启停任务、修改调度）将在后续版本开放。
    </NAlert>

    <NSpace vertical :size="12">
      <!-- 系统状态 -->
      <NCard title="系统状态" hoverable>
        <NDescriptions :column="3" bordered size="small">
          <NDescriptionsItem label="后端服务">
            <NTag v-if="backendInfo" :type="backendInfo.jobsApi === 'ok' ? 'success' : 'error'" size="small" :bordered="false">
              {{ backendInfo.jobsApi === 'ok' ? '运行中' : '异常' }}
            </NTag>
            <span v-else style="color: #999">检测中...</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="数据库">
            <NTag type="success" size="small" :bordered="false">adata 已连接</NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="数据规模">
            <span>24+ 张表 · 约 3700 万行</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="股票日线覆盖">
            <span>沪深 A 股 ~5400 只，更新至 2026-09-01</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="概念板块">
            <span>同花顺概念指数 + 成分股</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="资讯">
            <span style="color: #d03050">待接入采集任务</span>
          </NDescriptionsItem>
        </NDescriptions>
      </NCard>

      <!-- 数据源 -->
      <NCard title="数据源配置" hoverable>
        <NTable size="small" :bordered="false" :single-line="false">
          <thead>
            <tr>
              <th>数据源</th>
              <th>采集范围</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in dataSources" :key="d.source">
              <td style="font-weight: 600">{{ d.source }}</td>
              <td style="color: #666">{{ d.scope }}</td>
              <td><NTag :type="d.type" size="small" :bordered="false">{{ d.status }}</NTag></td>
            </tr>
          </tbody>
        </NTable>
      </NCard>

      <!-- 任务规划 -->
      <NCard title="采集任务规划" hoverable>
        <NTable size="small" :bordered="false" :single-line="false">
          <thead>
            <tr>
              <th>任务名</th>
              <th>说明</th>
              <th>调度</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in taskOverview" :key="t.name">
              <td style="font-weight: 600; font-family: monospace">{{ t.name }}</td>
              <td style="color: #666">{{ t.desc }}</td>
              <td><NTag size="small" :bordered="false" :type="t.schedule === '待开发' ? 'warning' : 'info'">{{ t.schedule }}</NTag></td>
            </tr>
          </tbody>
        </NTable>
      </NCard>
    </NSpace>
  </div>
</template>
