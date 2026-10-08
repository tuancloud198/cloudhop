<script setup>
import { computed } from 'vue'

import { date, money, timeAgo } from '../format.js'

const props = defineProps({
  // The budget the meter is measured against; null when there is none
  budget: { type: Object, default: null },
  // billing.spend from the API; null until a spend update has been received
  spend: { type: Object, default: null },
  // Why there is no spend yet: disconnected | no-subscription | waiting
  pending: { type: String, default: 'waiting' },
})

const DAY = 24 * 3600 * 1000

const currency = computed(() => props.spend?.currency || props.budget?.currency || '')
const amount = computed(() => Number(props.spend?.amount ?? props.budget?.amount ?? 0))
const spent = computed(() => Number(props.spend?.spent ?? 0))
const ratio = computed(() => (props.spend && amount.value > 0 ? spent.value / amount.value : 0))
const level = computed(() => {
  if (ratio.value >= 0.9) return 'danger'
  if (ratio.value >= 0.75) return 'warning'
  return 'ok'
})

// Start and end (exclusive) of the budget's current period, or null when unknown
const range = computed(() => {
  const budget = props.budget
  if (!budget) return null
  if (budget.period === 'custom') {
    if (!budget.start_date || !budget.end_date) return null
    // end_date is the last day counted
    return { start: utcDay(budget.start_date), end: utcDay(budget.end_date) + DAY }
  }
  const months = { month: 1, quarter: 3, year: 12 }[budget.period]
  if (!months) return null
  const now = new Date()
  const first = months === 12 ? 0 : now.getUTCMonth() - (now.getUTCMonth() % months)
  return {
    start: Date.UTC(now.getUTCFullYear(), first, 1),
    end: Date.UTC(now.getUTCFullYear(), first + months, 1),
  }
})

const elapsed = computed(() => {
  if (!range.value) return null
  const { start, end } = range.value
  return Math.min(Math.max((Date.now() - start) / (end - start), 0), 1)
})

const days = computed(() => {
  if (!range.value) return null
  const { start, end } = range.value
  const total = Math.round((end - start) / DAY)
  const today = Math.min(Math.max(Math.floor((Date.now() - start) / DAY) + 1, 1), total)
  return { today, total, end: new Date(end - DAY) }
})

// When the money runs out if spending goes on at the rate seen since the period started
const runOut = computed(() => {
  if (!props.spend || spent.value <= 0) return null
  const since = new Date(props.spend.as_of) - new Date(props.spend.interval_start)
  // A few hours of spend says little about the rate, and would raise false alarms
  if (since < DAY) return null
  const left = amount.value - spent.value
  if (left <= 0) return { at: new Date(props.spend.as_of), early: true }
  const at = new Date(new Date(props.spend.as_of).getTime() + (left / spent.value) * since)
  return { at, early: range.value ? at.getTime() < range.value.end : false }
})

const PENDING = {
  disconnected: 'This budget is not connected to a Pub/Sub topic, so GCP sends it no spend updates.',
  'no-subscription': 'Enter the Pub/Sub subscription below so Refresh billing can pull spend updates.',
  waiting: 'Waiting for GCP to send the first spend update; it sends one several times a day.',
}

function utcDay(value) {
  return new Date(`${value}T00:00:00Z`).getTime()
}

function percent(value) {
  return `${Math.round(value * 1000) / 10}%`
}

function dayString(value) {
  return date(value.toISOString().slice(0, 10))
}
</script>

<template>
  <div class="meter" :class="spend ? level : budget ? 'pending' : 'none'">
    <template v-if="budget || spend">
      <div class="figures">
        <span class="spent">{{ spend ? money(spent, currency) : '—' }}</span>
        <span class="muted">of {{ money(amount, currency) }} spent</span>
        <span v-if="spend" class="percent">{{ percent(ratio) }}</span>
        <span v-else class="badge plain waiting">awaiting first report</span>
      </div>

      <div
        class="track"
        role="meter"
        :aria-valuenow="spend ? spent : 0"
        :aria-valuetext="spend ? undefined : 'No spend reported yet'"
        aria-valuemin="0"
        :aria-valuemax="amount"
        :aria-label="`Spent of ${money(amount, currency)}`"
      >
        <span v-if="spend" class="fill" :style="{ width: percent(Math.min(ratio, 1)) }" />
        <span
          v-if="elapsed !== null"
          class="today"
          :style="{ left: percent(elapsed) }"
          :title="`${percent(elapsed)} of the budget period has passed`"
        />
      </div>

      <div class="facts">
        <span v-if="spend">
          <strong>{{ money(Math.max(amount - spent, 0), currency) }}</strong> left
        </span>
        <span v-if="days">
          Day {{ days.today }} of {{ days.total }}, ends {{ dayString(days.end) }}
        </span>
        <span v-if="runOut" :class="{ alarm: runOut.early }">
          <template v-if="runOut.early">At this pace, runs out around {{ dayString(runOut.at) }}</template>
          <template v-else>At this pace, lasts past the end of the period</template>
        </span>
      </div>

      <div class="muted small">
        <template v-if="spend">
          Budget <strong>{{ spend.budget_name }}</strong>, reported {{ timeAgo(spend.as_of) }}. Spend reaches GCP's
          reports hours after it happens. The line on the bar marks today.
        </template>
        <template v-else>
          Budget <strong>{{ budget.name || budget.external_id }}</strong>. {{ PENDING[pending] }}
          <RouterLink v-if="pending === 'disconnected'" :to="{ name: 'guide', hash: '#gcp-billing' }">How</RouterLink>
        </template>
      </div>
    </template>

    <template v-else>
      <div class="figures">
        <span class="spent">No budget</span>
      </div>
      <div class="track" />
      <div class="muted small">
        Set a budget whose amount is your credit total, and CloudHop shows how much of it is spent and left.
        <RouterLink :to="{ name: 'guide', hash: '#gcp-billing' }">Set one up</RouterLink>
      </div>
    </template>
  </div>
</template>

<style scoped>
.meter {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface-muted);
}

.figures {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  align-items: baseline;
}

.spent {
  font-size: 22px;
  font-weight: 650;
}

.pending .spent,
.none .spent {
  color: var(--text-muted);
}

.percent,
.waiting {
  margin-left: auto;
}

.percent {
  font-weight: 600;
}

.warning .percent {
  color: var(--warning);
}

.danger .percent {
  color: var(--danger);
}

.track {
  position: relative;
  height: 10px;
  overflow: hidden;
  border-radius: 5px;
  background: var(--border);
}

.fill {
  display: block;
  height: 100%;
  border-radius: 5px;
  background: var(--primary);
}

.warning .fill {
  background: var(--warning);
}

.danger .fill {
  background: var(--danger);
}

/* No report yet: the whole track waits, striped and drifting */
.pending .track {
  background: repeating-linear-gradient(
    -45deg,
    var(--border) 0 8px,
    var(--surface) 8px 16px
  );
  animation: drift 1.6s linear infinite;
}

/* No budget: an outline with nothing to fill */
.none .track {
  border: 1px dashed var(--text-muted);
  background: transparent;
}

.today {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: var(--meter-marker);
}

.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 16px;
  font-size: 13px;
}

.alarm {
  color: var(--danger);
  font-weight: 500;
}

.small {
  font-size: 12.5px;
}

@keyframes drift {
  to {
    /* One stripe period along x (16px / sin 45°), so the loop is seamless */
    background-position: 22.63px 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pending .track {
    animation: none;
  }
}
</style>
