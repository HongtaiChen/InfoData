<script setup lang="ts">
/**
 * EnvPlaceholderView —— 投资环境·未上线章节占位页
 * 章节骨架先立起来（侧栏可达、URL 可收藏），内容按版本增量补充；
 * 本章候选指标清单见 docs/design/ 投资环境模块章节化改版设计文档。
 */
import { useRoute } from 'vue-router'

const route = useRoute()
const title = route.meta?.title as string | undefined
</script>

<template>
  <div style="max-width:1280px;margin:0 auto;">
    <div style="background:#FFFFFF;border:1px solid #EDEFF2;border-radius:8px;padding:48px 24px;text-align:center;">
      <div style="font-size:34px;line-height:1;">{{ route.meta?.icon }}</div>
      <h2 style="font-size:17px;font-weight:700;color:#185FA5;margin:14px 0 8px;">{{ title }} · 建设中</h2>
      <p style="font-size:13px;color:#6B7280;line-height:1.7;margin:0;">
        本章骨架已就位（URL 可直达/收藏），指标清单与数据源将在后续版本增量上线。<br />
        当前可先使用「国内指数」章节。
      </p>
    </div>
  </div>
</template>
