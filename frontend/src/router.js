import { createRouter, createWebHistory } from 'vue-router'

import AccountView from './views/AccountView.vue'
import ClusterView from './views/ClusterView.vue'
import GuideView from './views/GuideView.vue'
import HomeView from './views/HomeView.vue'
import MoveView from './views/MoveView.vue'
import MovesView from './views/MovesView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/accounts/:id(\\d+)', name: 'account', component: AccountView, props: true },
    { path: '/clusters/:id(\\d+)', name: 'cluster', component: ClusterView, props: true },
    { path: '/moves', name: 'moves', component: MovesView },
    { path: '/moves/:id(\\d+)', name: 'move', component: MoveView, props: true },
    { path: '/guide', name: 'guide', component: GuideView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
