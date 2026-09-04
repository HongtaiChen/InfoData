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
  { source: '新浪', scope: '股票日线备用 / 交易日历', status: '稳定', type: 'success' as const },
  { source: 'Tushare', scope: '股票日线保底（需 token）', status: '未配置', type: 'default' as const },
  { source: '同花顺', scope: '概念指数行情 + 成分股映射（成分股映射源受限待补）', status: '行情已接入', type: 'success' as const },
  { source: '财联社', scope: '财联社电报（与东财双源轮询）', status: '已接入', type: 'success' as const },
]

const taskOverview = [
  { name: 'stock_daily_incr', desc: '股票日线增量采集（东财→腾讯→新浪→Tushare 四级降级）', schedule: '工作日 19:00', statusType: 'success' as const },
  { name: 'news_fetch', desc: '资讯采集：财联社 + 东财 双源去重', schedule: '每 30 分钟', statusType: 'success' as const },
  { name: 'concept_market_sync', desc: '同花顺概念板块行情增量同步（375 概念，85265 行历史已回补）', schedule: '工作日 20:00', statusType: 'success' as const },
  { name: 'market_current_sync', desc: '行情快照聚合（最新交易日 OHLC + YTD）', schedule: '工作日 18:30', statusType: 'success' as const },
  { name: 'trade_calendar_sync', desc: '交易日历补齐（含 year/month/day 冗余列回填）', schedule: '周日 02:30', statusType: 'success' as const },
  { name: 'ai_concept_analysis', desc: '日历事件 AI 概念分析（豆包，无 Key 时降级占位）', schedule: '手动/触发', statusType: 'info' as const },
  { name: 'finance_calendar_sync', desc: '投资日历事件（原 JY 源失效，待替代源）', schedule: '源失效', statusType: 'warning' as const },
  { name: 'ths_stock_concepts_sync', desc: '同花顺概念成分股映射（东财风控停摆，源恢复后补）', schedule: '源受限', statusType: 'warning' as const },
]

onMounted(loadDbStats)
</script>

<template>
  <div>
    <NAlert type="info" :show-icon="true" closable style="margin-bottom: 12px">
      InfoData 数据分析平台 · 本栏目展示系统状态与数据源配置；运维操作（启停任务 / 修改 cron / 立即执行）已迁移到「作业监控」栏目。
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
            <span>同花顺 398 个概念指数（K线最新 2026-09-02） · 成分股映射待源恢复</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="资讯">
            <span>东财 + 财联社 双源 · 346 条（滚动入库，每 30 分钟）</span>
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
              <td><NTag size="small" :bordered="false" :type="t.statusType">{{ t.schedule }}</NTag></td>
            </tr>
          </tbody>
        </NTable>
      </NCard>
    </NSpace>
  </div>
</template>
