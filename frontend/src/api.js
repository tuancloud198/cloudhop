// Thin client for the Django REST API under /api/v1 (proxied by Vite in development)

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message)
    this.status = status
    // Field errors from DRF, e.g. {credential_file: ["..."]}
    this.fields = body && typeof body === 'object' && !body.detail ? body : {}
  }
}

function csrfToken() {
  // Django enforces CSRF when the browser also holds an admin session
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : null
}

function errorMessage(body, response) {
  if (body?.detail) return body.detail
  if (body && typeof body === 'object') {
    return Object.entries(body)
      .map(([field, errors]) => `${field}: ${[].concat(errors).join(' ')}`)
      .join('\n')
  }
  if (response.status >= 500) {
    return `The API failed (${response.status}). Is the Django server running?`
  }
  return `${response.status} ${response.statusText}`
}

async function request(method, path, { body, params } = {}) {
  const headers = { Accept: 'application/json' }
  const init = { method, headers, credentials: 'same-origin' }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  if (method !== 'GET') {
    const token = csrfToken()
    if (token) headers['X-CSRFToken'] = token
  }

  let url = `/api/v1${path}`
  if (params) {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, value]) => value !== undefined && value !== null),
    )
    url += `?${query}`
  }

  let response
  try {
    response = await fetch(url, init)
  } catch {
    throw new ApiError('Cannot reach the CloudHop API. Is the Django server running?', 0, null)
  }
  if (response.status === 204) return null
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(errorMessage(data, response), response.status, data)
  return data
}

export const api = {
  listAccounts: () => request('GET', '/accounts/'),
  // form: FormData with name, provider and credential_file
  createAccount: (form) => request('POST', '/accounts/', { body: form }),
  deleteAccount: (id) => request('DELETE', `/accounts/${id}/`),

  listClusters: (accountId) => request('GET', `/accounts/${accountId}/clusters/`),
  syncClusters: (accountId) => request('POST', `/accounts/${accountId}/clusters/sync/`),
  getCluster: (id) => request('GET', `/clusters/${id}/`),

  // params: limit, offset, group, kind, namespace ("" = cluster-scoped), search
  listResources: (clusterId, params) => request('GET', `/clusters/${clusterId}/resources/`, { params }),
  resourceSummary: (clusterId) => request('GET', `/clusters/${clusterId}/resources/summary/`),
  syncResources: (clusterId) => request('POST', `/clusters/${clusterId}/resources/sync/`),

  getBilling: (accountId) => request('GET', `/accounts/${accountId}/billing/`),
  // Also returns warnings (what could not be read) and received (notifications stored);
  // replay first asks the provider again for the notifications it still keeps
  syncBilling: (accountId, { replay = false } = {}) =>
    request('POST', `/accounts/${accountId}/billing/sync/`, { body: { replay } }),
  // data: {pubsub_subscription}
  updateBillingAccount: (id, data) => request('PATCH', `/billing-accounts/${id}/`, { body: data }),
}
