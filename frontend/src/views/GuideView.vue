<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppIcon from '../components/AppIcon.vue'
import CodeBlock from '../components/CodeBlock.vue'
import ProviderLogo from '../components/ProviderLogo.vue'
import { accounts } from '../accounts.js'

defineEmits(['add-account'])

const route = useRoute()

const PROVIDER_OPTIONS = [
  { value: 'gcp', label: 'Google Cloud', ready: true },
  { value: 'aws', label: 'AWS', ready: false },
  { value: 'azure', label: 'Azure', ready: false },
]
// Anchors inside a provider's setup; jumping to one selects that provider
const PROVIDER_ANCHORS = { 'gcp-secrets': 'gcp' }

const provider = ref('gcp')
const selectedProvider = computed(() => PROVIDER_OPTIONS.find((option) => option.value === provider.value))

// GCP accounts the Secret RBAC example can be filled in for
const gcpAccounts = computed(() => accounts.items.filter((account) => account.provider === 'gcp'))
const selectedId = ref('')

watch(
  gcpAccounts,
  (items) => {
    if (!items.some((account) => String(account.id) === selectedId.value)) {
      selectedId.value = items.length ? String(items[0].id) : ''
    }
  },
  { immediate: true },
)

const selected = computed(() => gcpAccounts.value.find((account) => String(account.id) === selectedId.value))
const projectId = computed(() => selected.value?.project_id || 'PROJECT_ID')
const uniqueId = computed(() => selected.value?.external_id || 'SERVICE_ACCOUNT_UNIQUE_ID')

const SERVICE_ACCOUNT = 'cloudhop@PROJECT_ID.iam.gserviceaccount.com'

const enableApis = `gcloud services enable \\
  container.googleapis.com \\
  cloudresourcemanager.googleapis.com \\
  compute.googleapis.com \\
  --project PROJECT_ID`

const createServiceAccount = `gcloud iam service-accounts create cloudhop \\
  --project PROJECT_ID \\
  --display-name CloudHop

gcloud projects add-iam-policy-binding PROJECT_ID \\
  --member serviceAccount:${SERVICE_ACCOUNT} \\
  --role roles/container.viewer

# Optional: node CPU and memory on the account page
gcloud projects add-iam-policy-binding PROJECT_ID \\
  --member serviceAccount:${SERVICE_ACCOUNT} \\
  --role roles/compute.viewer`

const createKey = `gcloud iam service-accounts keys create cloudhop-key.json \\
  --iam-account ${SERVICE_ACCOUNT}`

const findUniqueId = `gcloud iam service-accounts describe ${SERVICE_ACCOUNT} \\
  --format 'value(uniqueId)'`

const secretReader = computed(() => `apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cloudhop-secret-reader
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: cloudhop-secret-reader
subjects:
  # The service account's numeric unique ID, not its email
  - kind: User
    name: "${uniqueId.value}"
    apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: ClusterRole
  name: cloudhop-secret-reader
  apiGroup: rbac.authorization.k8s.io
`)

const applySecretReader = computed(() => `gcloud container clusters get-credentials CLUSTER_NAME \\
  --location LOCATION \\
  --project ${projectId.value}

kubectl apply -f cloudhop-secret-reader.yaml`)

// The page scrolls inside .main, not the window, so the router cannot jump to #anchors itself
async function scrollToHash() {
  if (!route.hash) return
  const anchor = route.hash.slice(1)
  if (PROVIDER_ANCHORS[anchor]) provider.value = PROVIDER_ANCHORS[anchor]
  await nextTick()
  document.getElementById(anchor)?.scrollIntoView({ block: 'start' })
}

onMounted(scrollToHash)
watch(() => route.hash, scrollToHash)
</script>

<template>
  <div class="page guide">
    <header>
      <h1>Guide</h1>
      <p class="lead">How CloudHop works, and how to prepare a cloud account so it can read everything it needs.</p>
      <nav class="toc" aria-label="On this page">
        <RouterLink :to="{ hash: '#how-it-works' }">How it works</RouterLink>
        <RouterLink :to="{ hash: '#getting-started' }">Getting started</RouterLink>
        <RouterLink :to="{ hash: '#providers' }">Provider setup</RouterLink>
        <RouterLink :to="{ hash: '#gcp-secrets' }">Reading Secrets on GKE</RouterLink>
      </nav>
    </header>

    <section id="how-it-works" class="card">
      <h2>How it works</h2>
      <p>
        CloudHop copies the Kubernetes objects your team created out of a cluster and keeps them as clean manifests,
        so they can be mirrored to clusters in other accounts. It runs on your machine only, and it only reads from
        the cloud: it never changes anything in a project or a cluster.
      </p>

      <div class="levels">
        <div class="level">
          <AppIcon name="cloud" :size="18" />
          <div>
            <h3>Account</h3>
            <p>
              One cloud project, reached with a service account key. The key is saved on the machine running
              CloudHop and checked against the project when you add it.
            </p>
          </div>
        </div>
        <div class="level">
          <AppIcon name="server" :size="18" />
          <div>
            <h3>Clusters</h3>
            <p>
              <strong>Sync clusters</strong> lists the account's Kubernetes clusters in every location, with their
              version, node pools and machine sizes.
            </p>
          </div>
        </div>
        <div class="level">
          <AppIcon name="box" :size="18" />
          <div>
            <h3>Resources</h3>
            <p>
              <strong>Refresh resources</strong> connects to the cluster's API server and reads every built-in kind
              at its preferred version. Each refresh replaces what was stored for that cluster.
            </p>
          </div>
        </div>
      </div>

      <h3>What gets stored</h3>
      <ul>
        <li>
          <strong>Only objects people created.</strong> CloudHop leaves out what the cluster makes for itself:
          <code>kube-*</code> namespaces and the provider's own (<code>gke-*</code> and <code>gmp-*</code> on GKE),
          objects owned by another object (Pods of a Deployment, Jobs of a CronJob, …), built-in RBAC
          (<code>system:*</code>), the <code>default</code> ServiceAccount, <code>kube-root-ca.crt</code>, and volumes a
          provisioner created for a claim.
        </li>
        <li>
          <strong>Some kinds are never read</strong> because they change every few seconds or describe the cluster's
          own machinery: Events, Endpoints, EndpointSlices, Leases, Nodes and the like.
        </li>
        <li>
          <strong>Built-in kinds only.</strong> CustomResourceDefinitions are kept, but the objects of custom resources
          are not read yet.
        </li>
        <li>
          <strong>Manifests are cleaned so they can be applied elsewhere.</strong> Fields the API server sets
          (<code>uid</code>, <code>resourceVersion</code>, <code>creationTimestamp</code>, <code>managedFields</code>)
          and the <code>last-applied-configuration</code> annotation are removed. <code>status</code> is kept apart, on
          the Status tab.
        </li>
        <li>
          <strong>Secrets keep their values</strong>, base64-encoded as Kubernetes returns them. Open a Secret and use
          <strong>Decode values</strong> to read them. Treat the CloudHop database as sensitive.
        </li>
      </ul>

      <h3>When a kind cannot be read</h3>
      <p>
        If the service account is not allowed to list a kind, or the kind went away, that kind is skipped and named in
        a warning on the cluster page; the rest of the refresh goes on. Any other error stops the refresh, and what was
        stored before is kept.
      </p>
    </section>

    <section id="getting-started" class="card">
      <h2>Getting started</h2>
      <ol class="steps">
        <li>
          <strong>Prepare the provider.</strong> Create a service account with the right roles, as described for your
          provider below.
        </li>
        <li>
          <strong>Add the account.</strong> Use <strong>Add</strong> in the sidebar, pick the provider and drop in the
          key file. CloudHop reads the project from the key and checks it can reach it.
          <div class="inline-action">
            <button class="btn btn-sm" @click="$emit('add-account')">
              <AppIcon name="plus" :size="14" />
              Add account
            </button>
          </div>
        </li>
        <li><strong>Sync clusters</strong> from the account page.</li>
        <li>
          <strong>Refresh resources</strong> from a cluster's page. Filter by kind or namespace, and click a row to see
          its manifest and copy it as YAML.
        </li>
      </ol>
    </section>

    <section id="providers" class="card">
      <div class="provider-heading">
        <span class="provider"><ProviderLogo :provider="provider" :size="22" /></span>
        <h2>Provider setup</h2>
        <select v-model="provider" class="select" aria-label="Provider">
          <option v-for="option in PROVIDER_OPTIONS" :key="option.value" :value="option.value">
            {{ option.label }}{{ option.ready ? '' : ' (coming soon)' }}
          </option>
        </select>
      </div>

      <div v-if="!selectedProvider.ready" class="empty coming-soon">
        <h3>{{ selectedProvider.label }} is coming soon</h3>
        <p>CloudHop can only connect to Google Cloud for now. Setup steps for {{ selectedProvider.label }} will be added here.</p>
      </div>

      <template v-else>
        <p>
          CloudHop signs in as a Google Cloud service account with a JSON key. Each GCP account in CloudHop is one
          project. Replace <code>PROJECT_ID</code> in the commands below with yours.
        </p>

        <ol class="steps">
          <li>
            <strong>Enable the APIs</strong> CloudHop calls: Kubernetes Engine, Cloud Resource Manager (to check the
            project) and Compute Engine (for machine sizes).
            <CodeBlock :code="enableApis" label="shell" />
          </li>
          <li>
            <strong>Create the service account and grant its roles.</strong>
            <div class="table-wrap roles">
              <table class="table">
                <thead>
                  <tr>
                    <th>Role</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><code>roles/container.viewer</code><span class="badge warning">Required</span></td>
                    <td>Reads the project, lists clusters, and reads the objects inside them, except Secrets.</td>
                  </tr>
                  <tr>
                    <td><code>roles/compute.viewer</code><span class="badge plain">Optional</span></td>
                    <td>Looks up CPU and memory of node machine types. Without it, sizes are left blank.</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <CodeBlock :code="createServiceAccount" label="shell" />
          </li>
          <li>
            <strong>Create a key</strong> and add it to CloudHop as a new account.
            <CodeBlock :code="createKey" label="shell" />
          </li>
          <li>
            <strong>Allow it to read Secrets</strong> in each cluster, as described next.
          </li>
        </ol>

        <div id="gcp-secrets" class="attention">
          <h3>
            <AppIcon name="alert" :size="16" />
            Reading Secrets on GKE
          </h3>
          <p>
            Kubernetes Engine Viewer can read every object in a cluster except Secrets. Without the step below, the
            refresh skips the Secret kind with a <em>forbidden</em> warning, and Secrets cannot be mirrored. Grant it
            inside each cluster with Kubernetes RBAC: a ClusterRole that can get and list Secrets, bound to the service
            account.
          </p>
          <p>
            <strong>Bind the numeric unique ID, not the email.</strong> CloudHop's token does not carry the email scope,
            so GKE knows the service account only by its unique ID; a binding to the email has no effect. The ID is
            <code>client_id</code> in the key file and <strong>ID</strong> on the account page in CloudHop, or run:
          </p>
          <CodeBlock :code="findUniqueId" label="shell" />

          <div class="field picker">
            <label for="guide-account">Fill in for account</label>
            <select id="guide-account" v-model="selectedId" class="select" :disabled="!gcpAccounts.length">
              <option v-if="!gcpAccounts.length" value="">No Google Cloud account added yet</option>
              <option v-for="account in gcpAccounts" :key="account.id" :value="String(account.id)">
                {{ account.name }} ({{ account.external_id }})
              </option>
            </select>
          </div>

          <CodeBlock :code="secretReader" label="cloudhop-secret-reader.yaml" />

          <p>
            Apply it to every cluster CloudHop should read Secrets from. You need to be allowed to create RBAC objects in
            the cluster, e.g. with <code>roles/container.admin</code>.
          </p>
          <CodeBlock :code="applySecretReader" label="shell" />
          <p>Then use <strong>Refresh resources</strong> on the cluster: Secrets are read with their values.</p>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.guide {
  max-width: 920px;
}

.guide section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px 24px 24px;
  /* Keeps a heading clear of the mobile top bar when jumped to */
  scroll-margin-top: 64px;
}

.guide p,
.guide ul,
.guide ol {
  margin: 0;
}

.guide h3 {
  margin: 4px 0 0;
  font-size: 15px;
}

.lead {
  margin-top: 6px;
  color: var(--text-muted);
}

.toc {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  margin-top: 12px;
  font-size: 13px;
}

.levels {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.level {
  display: flex;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface-muted);
}

.level > svg {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--primary);
}

.level h3 {
  margin: 0 0 2px;
}

ul,
.steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 22px;
}

.steps > li > * + * {
  margin-top: 10px;
}

.inline-action {
  display: flex;
}

.provider-heading {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.provider-heading h2 {
  flex: 1;
}

.coming-soon {
  padding: 32px 16px;
}

.provider {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.roles {
  border: 1px solid var(--border);
  border-radius: 6px;
}

.roles td:last-child {
  white-space: normal;
}

.roles .badge {
  margin-left: 8px;
}

.attention {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 18px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--warning);
  border-radius: 6px;
  background: var(--warning-soft);
  scroll-margin-top: 64px;
}

.attention h3 {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 0;
  color: var(--warning);
}

.picker {
  max-width: 420px;
}

@media (max-width: 720px) {
  .guide section {
    padding: 16px;
  }
}
</style>
