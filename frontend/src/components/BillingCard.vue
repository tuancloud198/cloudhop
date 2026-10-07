<script setup>
import { computed, ref, watch } from 'vue'

import AppIcon from './AppIcon.vue'
import SpendMeter from './SpendMeter.vue'
import { api } from '../api.js'
import { date, dateTime, money, timeAgo } from '../format.js'
import { notify } from '../toasts.js'

const props = defineProps({ account: { type: Object, required: true } })

const CREDIT_TREATMENTS = {
  EXCLUDE_ALL_CREDITS: 'credits excluded',
  INCLUDE_ALL_CREDITS: 'credits included',
  INCLUDE_SPECIFIED_CREDITS: 'some credits included',
}
const PERIODS = { month: 'Monthly', quarter: 'Quarterly', year: 'Yearly' }

const billing = ref(null)
const loading = ref(false)
const loadError = ref(null)
const syncing = ref(false)
// What the last refresh could not read; not stored, so only shown after a refresh in this page
const warnings = ref([])
const subscription = ref('')
const savingSubscription = ref(false)

const billingAccount = computed(() => billing.value?.billing_account)
// What the meter measures: the budget spend_status picked, or before any spend
// arrives, the covering budget that looks like a credit grant (credits excluded)
const primaryBudget = computed(() => {
  const budgets = billingAccount.value?.budgets ?? []
  if (billing.value?.spend) return budgets.find((budget) => budget.id === billing.value.spend.budget_id) ?? null
  const covering = budgets.filter((budget) => budget.covers_account !== false && budget.amount !== null)
  return covering.find((budget) => budget.credit_treatment === 'EXCLUDE_ALL_CREDITS') ?? covering[0] ?? null
})
const subscriptionChanged = computed(
  () => billingAccount.value && subscription.value.trim() !== billingAccount.value.pubsub_subscription,
)

watch(() => props.account.id, load, { immediate: true })

async function load() {
  loading.value = true
  loadError.value = null
  billing.value = null
  warnings.value = []
  try {
    show(await api.getBilling(props.account.id))
  } catch (error) {
    loadError.value = error.message
  } finally {
    loading.value = false
  }
}

function show(data) {
  billing.value = data
  subscription.value = data.billing_account?.pubsub_subscription ?? ''
}

async function refresh() {
  syncing.value = true
  try {
    const result = await api.syncBilling(props.account.id)
    show(result)
    warnings.value = result.warnings
    const received = result.received ? `, ${result.received} spend update${result.received === 1 ? '' : 's'} received` : ''
    notify(`Billing refreshed${received}`, result.warnings.length ? 'info' : 'success')
  } catch (error) {
    notify(`Billing refresh failed: ${error.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

async function saveSubscription() {
  savingSubscription.value = true
  try {
    const updated = await api.updateBillingAccount(billingAccount.value.id, {
      pubsub_subscription: subscription.value.trim(),
    })
    billing.value.billing_account = { ...billingAccount.value, pubsub_subscription: updated.pubsub_subscription }
    subscription.value = updated.pubsub_subscription
    notify('Subscription saved. Use Refresh billing to pull spend updates.', 'success')
  } catch (error) {
    notify(`Could not save the subscription: ${error.fields.pubsub_subscription?.[0] ?? error.message}`, 'error')
  } finally {
    savingSubscription.value = false
  }
}

// Why a budget has reported no spend: disconnected | no-subscription | waiting
function pendingState(budget) {
  if (!budget?.pubsub_topic) return 'disconnected'
  if (!billingAccount.value.pubsub_subscription) return 'no-subscription'
  return 'waiting'
}

function ratio(spent, amount) {
  return Number(amount) > 0 ? Number(spent) / Number(amount) : 0
}

function level(value) {
  if (value >= 0.9) return 'danger'
  if (value >= 0.75) return 'warning'
  return 'ok'
}

function percent(value) {
  return `${Math.round(value * 100)}%`
}

function period(budget) {
  if (budget.period !== 'custom') return PERIODS[budget.period] ?? 'Period unknown'
  return `${date(budget.start_date)} – ${budget.end_date ? date(budget.end_date) : 'no end'}`
}
</script>

<template>
  <section class="card">
    <div class="section-header">
      <div>
        <h2>Billing</h2>
        <span v-if="billing?.synced_at" class="muted small" :title="dateTime(billing.synced_at)">
          Refreshed {{ timeAgo(billing.synced_at) }}
        </span>
      </div>
      <button class="btn btn-sm" :disabled="syncing || loading || !account.is_valid" @click="refresh">
        <span v-if="syncing" class="spinner" />
        <AppIcon v-else name="refresh" :size="14" />
        {{ syncing ? 'Refreshing…' : 'Refresh billing' }}
      </button>
    </div>

    <div v-if="loading" class="empty"><span class="spinner" /></div>
    <div v-else-if="loadError" class="body"><div class="alert danger">{{ loadError }}</div></div>

    <div v-else-if="!billing?.synced_at" class="empty">
      <AppIcon name="wallet" :size="32" />
      <h3>Billing not read yet</h3>
      <p>
        Use <strong>Refresh billing</strong> to find the billing account paying for this account and its budgets.
        <br />
        <RouterLink :to="{ name: 'guide', hash: '#gcp-billing' }">How to set up a budget</RouterLink>
      </p>
    </div>

    <div v-else-if="!billingAccount" class="empty">
      <AppIcon name="wallet" :size="32" />
      <h3>No billing account</h3>
      <p>This project has no billing account linked, so it has nothing to spend.</p>
    </div>

    <div v-else class="body">
      <div v-if="warnings.length" class="alert warning">
        <AppIcon name="alert" />
        <div>
          <strong>Some billing details could not be read:</strong>
          <ul>
            <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
          </ul>
          <RouterLink :to="{ name: 'guide', hash: '#gcp-billing' }">Which access CloudHop needs</RouterLink>
        </div>
      </div>

      <div class="meta billing-meta">
        <span>Billing account <strong>{{ billingAccount.name || billingAccount.external_id }}</strong></span>
        <code v-if="billingAccount.name">{{ billingAccount.external_id }}</code>
        <span v-if="billingAccount.currency">{{ billingAccount.currency }}</span>
        <span v-if="!billingAccount.is_open" class="badge danger">closed</span>
        <span v-if="!billing.billing_enabled" class="badge warning">billing disabled for this project</span>
      </div>

      <SpendMeter :budget="primaryBudget" :spend="billing.spend" :pending="pendingState(primaryBudget)" />

      <div class="budgets">
        <h3>Budgets</h3>
        <p v-if="!billingAccount.budgets.length" class="muted">
          No budgets found on this billing account, or CloudHop cannot list them.
          <RouterLink :to="{ name: 'guide', hash: '#gcp-billing' }">Set one up</RouterLink>
        </p>
        <div v-for="budget in billingAccount.budgets" :key="budget.id" class="budget">
          <div class="budget-head">
            <strong>{{ budget.name || budget.external_id }}</strong>
            <span class="muted">{{ money(budget.amount, budget.currency) }} · {{ period(budget) }}</span>
            <span v-if="CREDIT_TREATMENTS[budget.credit_treatment]" class="badge plain">
              {{ CREDIT_TREATMENTS[budget.credit_treatment] }}
            </span>
            <span v-if="budget.covers_account === false" class="badge plain">other projects</span>
            <span v-if="!budget.latest_status && pendingState(budget) === 'disconnected'" class="badge warning">
              not connected to Pub/Sub
            </span>
          </div>
          <template v-if="budget.latest_status">
            <div class="bar" :class="level(ratio(budget.latest_status.cost_amount, budget.latest_status.budget_amount))">
              <span
                :style="{
                  width: percent(Math.min(ratio(budget.latest_status.cost_amount, budget.latest_status.budget_amount), 1)),
                }"
              />
            </div>
            <div class="muted small">
              {{ money(budget.latest_status.cost_amount, budget.latest_status.currency) }} of
              {{ money(budget.latest_status.budget_amount, budget.latest_status.currency) }}
              ({{ percent(ratio(budget.latest_status.cost_amount, budget.latest_status.budget_amount)) }}),
              reported {{ timeAgo(budget.latest_status.published_at) }}
            </div>
          </template>
          <div v-else-if="pendingState(budget) === 'disconnected'" class="muted small">
            This budget sends no spend updates. Connect it to a Pub/Sub topic in GCP, then refresh billing.
            <RouterLink :to="{ name: 'guide', hash: '#gcp-billing' }">How</RouterLink>
          </div>
          <div v-else-if="pendingState(budget) === 'no-subscription'" class="muted small">
            Sends updates to <code>{{ budget.pubsub_topic }}</code>. Enter a subscription on that topic below so
            <strong>Refresh billing</strong> can pull them.
          </div>
          <div v-else class="muted small">
            No spend reported yet. GCP sends updates to <code>{{ budget.pubsub_topic }}</code> several times a day.
          </div>
        </div>
      </div>

      <form class="field subscription" @submit.prevent="saveSubscription">
        <label for="billing-subscription">Pub/Sub subscription for budget notifications</label>
        <div class="subscription-row">
          <input
            id="billing-subscription"
            v-model="subscription"
            class="input"
            placeholder="projects/PROJECT_ID/subscriptions/cloudhop-budget"
            spellcheck="false"
          />
          <button class="btn" type="submit" :disabled="!subscriptionChanged || savingSubscription">Save</button>
        </div>
        <span class="muted small">
          Spend comes only from these notifications; <strong>Refresh billing</strong> pulls the pending ones. Shared by
          every account on this billing account.
        </span>
      </form>
    </div>
  </section>
</template>

<style scoped>
.small {
  font-size: 12.5px;
}

.body {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 16px;
}

.body h3 {
  margin-bottom: 8px;
  font-size: 14px;
}

.empty > svg {
  color: var(--text-muted);
}

.alert ul {
  margin: 4px 0;
  padding-left: 18px;
}

.billing-meta {
  margin-top: 0;
}

.bar {
  height: 8px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--border);
}

.bar > span {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--primary);
}


.bar.warning > span {
  background: var(--warning);
}

.bar.danger > span {
  background: var(--danger);
}

.budgets {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.budgets h3 {
  margin-bottom: 0;
}

.budget {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.budget-head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  align-items: center;
}

.subscription-row {
  display: flex;
  gap: 8px;
}

.subscription-row .input {
  flex: 1;
  min-width: 0;
}
</style>
