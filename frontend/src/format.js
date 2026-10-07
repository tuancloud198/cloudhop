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

export function apiVersion(resource) {
  return resource.group ? `${resource.group}/${resource.version}` : resource.version
}

export const PROVIDERS = {
  gcp: 'Google Cloud',
  aws: 'AWS',
  azure: 'Azure',
}
