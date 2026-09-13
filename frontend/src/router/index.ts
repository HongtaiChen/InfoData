import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      children: [
        { path: '', redirect: '/market' },
        { path: 'market', name: 'market', component: () => import('@/views/MarketView.vue'), meta: { title: '行情看板', icon: '📈' } },
        { path: 'concept', name: 'concept', component: () => import('@/views/ConceptView.vue'), meta: { title: '概念板块', icon: '🧩' } },
        { path: 'calendar', name: 'calendar', component: () => import('@/views/CalendarView.vue'), meta: { title: '投资日历', icon: '📅' } },
        { path: 'news', name: 'news', component: () => import('@/views/NewsView.vue'), meta: { title: '资讯浏览', icon: '📰' } },
        { path: 'analysis', name: 'analysis', component: () => import('@/views/AnalysisOverview.vue'), meta: { title: '分析研究', icon: '🔬' } },
        // 分析模块详情（注册式扩展：后端 app/analysis/registry.py + 前端模块视图映射）
        { path: 'analysis/:moduleId', name: 'analysis-module', component: () => import('@/views/AnalysisModuleView.vue'), meta: { title: '分析详情', icon: '🔬' } },
        { path: 'jobs', name: 'jobs', component: () => import('@/views/JobsView.vue'), meta: { title: '作业监控', icon: '⚙️' } },
        { path: 'data', name: 'data', component: () => import('@/views/DataView.vue'), meta: { title: '数据中心', icon: '🗄️' } },
        { path: 'quality', name: 'quality', component: () => import('@/views/QualityView.vue'), meta: { title: '数据质量', icon: '🩺' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '系统设置', icon: '🔧' } },
      ],
    },
  ],
})

export default router