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

export function apiVersion(resource) {
  return resource.group ? `${resource.group}/${resource.version}` : resource.version
}

export const PROVIDERS = {
  gcp: 'Google Cloud',
  aws: 'AWS',
  azure: 'Azure',
}
