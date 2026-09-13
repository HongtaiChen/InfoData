<script setup lang="ts">
/**
 * RankTable —— 通用排行/明细表（naive-ui NDataTable 轻封装，分析模块明细区统一用）
 */
import { NDataTable, type DataTableColumns } from 'naive-ui'

const props = defineProps<{
  columns: DataTableColumns<any>
  rows: any[]
  loading?: boolean
  maxHeight?: string
}>()
</script>

<template>
  <NDataTable
    :columns="props.columns"
    :data="props.rows"
    :loading="props.loading"
    :max-height="props.maxHeight"
    :bordered="false"
    size="small"
    :pagination="false"
  />
</template>
