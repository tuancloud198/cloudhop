<script setup>
import { computed } from 'vue'

import awsDark from '../assets/providers/aws-dark.svg'
import aws from '../assets/providers/aws.svg'
import azure from '../assets/providers/azure.svg'
import gcp from '../assets/providers/gcp.svg'
import { PROVIDERS } from '../format.js'

// dark: variant for dark backgrounds, when the logo has dark parts
const LOGOS = {
  gcp: { light: gcp },
  aws: { light: aws, dark: awsDark },
  azure: { light: azure },
}

const props = defineProps({
  provider: { type: String, required: true },
  size: { type: Number, default: 20 },
})

const logo = computed(() => LOGOS[props.provider])
const label = computed(() => PROVIDERS[props.provider] ?? props.provider)
</script>

<template>
  <span class="logo" :style="{ width: `${size}px`, height: `${size}px` }">
    <template v-if="logo">
      <img :src="logo.light" :alt="label" :class="{ 'light-only': logo.dark }" />
      <img v-if="logo.dark" :src="logo.dark" :alt="label" class="dark-only" />
    </template>
    <span v-else class="fallback">{{ provider.slice(0, 3).toUpperCase() }}</span>
  </span>
</template>

<style scoped>
.logo {
  display: inline-grid;
  flex-shrink: 0;
  place-items: center;
}

img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.fallback {
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 700;
}

/* Same rule as the color tokens: the OS decides unless a theme was picked */
.dark-only {
  display: none;
}

:root[data-theme='dark'] .light-only {
  display: none;
}

:root[data-theme='dark'] .dark-only {
  display: block;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) .light-only {
    display: none;
  }

  :root:not([data-theme='light']) .dark-only {
    display: block;
  }
}
</style>
