import { createRouter, createWebHistory } from 'vue-router'

import AccountView from './views/AccountView.vue'
import ClusterView from './views/ClusterView.vue'
import HomeView from './views/HomeView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/accounts/:id(\\d+)', name: 'account', component: AccountView, props: true },
    { path: '/clusters/:id(\\d+)', name: 'cluster', component: ClusterView, props: true },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
