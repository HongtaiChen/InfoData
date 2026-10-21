<script setup lang="ts">
/**
 * EnvLayout —— 投资环境章节化布局（2026-10-20 定稿：侧边栏细分方案）
 *
 * 职责只有「透传 RouterView」：章节切换由主侧栏二级菜单完成（MainLayout 从
 * 嵌套路由自动生成），章节内锚点导航由各章节视图自己负责。两级职责：
 *   主侧栏 = 换章节（路由级），页内锚点 = 章节内跳转（滚动级）。
 * 单独成 layout 而非直接挂 component，是为了给后续章节共用的
 * 顶部说明/切换器预留挂载点，避免将来再动 router 骨架。
 */
</script>

<template>
  <RouterView />
</template>
