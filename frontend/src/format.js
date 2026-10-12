const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const absolute = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

const UNITS = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
  ['second', 1],
]

export function timeAgo(value) {
  if (!value) return 'never'
  const seconds = (new Date(value) - Date.now()) / 1000
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size || unit === 'second') {
      return relative.format(Math.round(seconds / size), unit)
    }
  }
}

export function dateTime(value) {
  return value ? absolute.format(new Date(value)) : ''
}

// A date without time (YYYY-MM-DD), shown as the same calendar day in every time zone
const dayOnly = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC' })

export function date(value) {
  return value ? dayOnly.format(new Date(`${value}T00:00:00Z`)) : ''
}

// value: number or decimal string from the API; currency: ISO code, may be empty
export function money(value, currency) {
  if (value === null || value === undefined || value === '') return '–'
  const number = Number(value)
  if (!currency) return number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return number.toLocaleString(undefined, { style: 'currency', currency })
}

const BYTE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

// Bytes as Kubernetes sizes them, e.g. 4.0 GiB
export function bytes(count) {
  let unit = 0
  while (count >= 1024 && unit < BYTE_UNITS.length - 1) {
    count /= 1024
    unit += 1
  }
  return unit ? `${count.toFixed(1)} ${BYTE_UNITS[unit]}` : `${count} B`
}

const QUANTITY_UNITS = {
  Ki: 2 ** 10, Mi: 2 ** 20, Gi: 2 ** 30, Ti: 2 ** 40, Pi: 2 ** 50,
  k: 1e3, M: 1e6, G: 1e9, T: 1e12, P: 1e15,
}

// A Kubernetes quantity ("8Gi", "500M", "1e9") in bytes; 0 if it cannot be read
export function quantityBytes(value) {
  const match = /^(\d+(?:\.\d+)?(?:e\d+)?)([KMGTP]i|[kMGTP])?$/.exec(String(value ?? '').trim())
  return match ? Number(match[1]) * (QUANTITY_UNITS[match[2]] ?? 1) : 0
}

export function apiVersion(resource) {
  return resource.group ? `${resource.group}/${resource.version}` : resource.version
}

export const PROVIDERS = {
  gcp: 'Google Cloud',
  aws: 'AWS',
  azure: 'Azure',
}
