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
        { path: 'analysis', name: 'analysis', component: () => import('@/views/AnalysisView.vue'), meta: { title: '分析研究', icon: '🔬' } },
        { path: 'jobs', name: 'jobs', component: () => import('@/views/JobsView.vue'), meta: { title: '作业监控', icon: '⚙️' } },
        { path: 'data', name: 'data', component: () => import('@/views/DataView.vue'), meta: { title: '数据浏览', icon: '🗄️' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '系统设置', icon: '🔧' } },
      ],
    },
  ],
})

export default router