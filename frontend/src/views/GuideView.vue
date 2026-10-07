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
const PROVIDER_ANCHORS = { 'gcp-secrets': 'gcp', 'gcp-billing': 'gcp' }

const provider = ref('gcp')
const selectedProvider = computed(() => PROVIDER_OPTIONS.find((option) => option.value === provider.value))

// GCP accounts the commands can be filled in for; "" keeps the placeholders, for a project not added yet
const gcpAccounts = computed(() => accounts.items.filter((account) => account.provider === 'gcp'))
const selectedId = ref(null)

watch(
  gcpAccounts,
  (items) => {
    // Pick the first account once loaded, and move off one that was deleted
    const known = items.some((account) => String(account.id) === selectedId.value)
    if (selectedId.value === null || (selectedId.value !== '' && !known)) {
      selectedId.value = items.length ? String(items[0].id) : ''
    }
  },
  { immediate: true },
)

const selected = computed(() => gcpAccounts.value.find((account) => String(account.id) === selectedId.value))
const projectId = computed(() => selected.value?.project_id || 'PROJECT_ID')
const uniqueId = computed(() => selected.value?.external_id || 'SERVICE_ACCOUNT_UNIQUE_ID')

function fill(command) {
  return command.replaceAll('PROJECT_ID', projectId.value)
}

const SERVICE_ACCOUNT = 'cloudhop@PROJECT_ID.iam.gserviceaccount.com'

const enableApis = `gcloud services enable \\
  container.googleapis.com \\
  cloudresourcemanager.googleapis.com \\
  compute.googleapis.com \\
  cloudbilling.googleapis.com \\
  billingbudgets.googleapis.com \\
  pubsub.googleapis.com \\
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

const findBillingAccount = `gcloud billing projects describe PROJECT_ID \\
  --format 'value(billingAccountName)'`

const createSubscription = `gcloud pubsub topics create cloudhop-budget --project PROJECT_ID

gcloud pubsub subscriptions create cloudhop-budget \\
  --topic cloudhop-budget \\
  --project PROJECT_ID`

const createBudget = `gcloud billing budgets create \\
  --billing-account BILLING_ACCOUNT_ID \\
  --display-name cloudhop-credits \\
  --budget-amount CREDIT_TOTAL \\
  --credit-types-treatment exclude-all-credits \\
  --start-date CREDITS_START_DATE \\
  --end-date CREDITS_END_DATE \\
  --threshold-rule percent=0.5 \\
  --threshold-rule percent=0.9 \\
  --notifications-rule-pubsub-topic projects/PROJECT_ID/topics/cloudhop-budget`

const connectBudget = `gcloud billing budgets update BUDGET_ID \\
  --billing-account BILLING_ACCOUNT_ID \\
  --notifications-rule-pubsub-topic projects/PROJECT_ID/topics/cloudhop-budget`

const grantBilling = `gcloud pubsub subscriptions add-iam-policy-binding cloudhop-budget \\
  --project PROJECT_ID \\
  --member serviceAccount:${SERVICE_ACCOUNT} \\
  --role roles/pubsub.subscriber

# Recommended: budget amounts, periods and scope, and the billing account's name
gcloud billing accounts add-iam-policy-binding BILLING_ACCOUNT_ID \\
  --member serviceAccount:${SERVICE_ACCOUNT} \\
  --role roles/billing.viewer`

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
        <RouterLink :to="{ hash: '#gcp-billing' }">Spend and credits on Google Cloud</RouterLink>
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
        <div class="level">
          <AppIcon name="wallet" :size="18" />
          <div>
            <h3>Billing</h3>
            <p>
              <strong>Refresh billing</strong> finds the billing account paying for the account, its budgets, and how
              much has been spent against them. Several accounts can share one billing account and its credits.
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
        <li>
          <strong>Refresh billing</strong> from the account page, once a budget is set up for the provider, to follow how
          much of it is spent.
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
          project. The commands below name the service account <code>cloudhop</code>; use yours if it has another
          name.
        </p>

        <div class="field picker">
          <label for="guide-account">Fill in the commands for</label>
          <select id="guide-account" v-model="selectedId" class="select">
            <option v-for="account in gcpAccounts" :key="account.id" :value="String(account.id)">
              {{ account.name }} ({{ account.project_id }})
            </option>
            <option value="">A project not added yet (keep PROJECT_ID)</option>
          </select>
        </div>

        <ol class="steps">
          <li>
            <strong>Enable the APIs</strong> CloudHop calls, in the service account's project: Kubernetes Engine,
            Cloud Resource Manager (to check the project), Compute Engine (for machine sizes), and Cloud Billing,
            Billing Budget and Pub/Sub (for spend).
            <CodeBlock :code="fill(enableApis)" label="shell" />
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
            <CodeBlock :code="fill(createServiceAccount)" label="shell" />
          </li>
          <li>
            <strong>Create a key</strong> and add it to CloudHop as a new account.
            <CodeBlock :code="fill(createKey)" label="shell" />
          </li>
          <li>
            <strong>Allow it to read Secrets</strong> in each cluster, and <strong>set up a budget</strong> to follow
            spend, as described next.
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
          <CodeBlock :code="fill(findUniqueId)" label="shell" />

          <CodeBlock :code="secretReader" label="cloudhop-secret-reader.yaml" />

          <p>
            Apply it to every cluster CloudHop should read Secrets from. You need to be allowed to create RBAC objects in
            the cluster, e.g. with <code>roles/container.admin</code>.
          </p>
          <CodeBlock :code="applySecretReader" label="shell" />
          <p>Then use <strong>Refresh resources</strong> on the cluster: Secrets are read with their values.</p>
        </div>

        <div id="gcp-billing" class="subsection">
          <h3>
            <AppIcon name="wallet" :size="16" />
            Spend and credits
          </h3>
          <p>
            Credits and budgets belong to the Cloud Billing account, and every project linked to it shares them. GCP has
            no API that returns current spend or the remaining credit balance, so CloudHop follows a
            <strong>budget</strong>: you set one whose amount is your credit total, connect it to Pub/Sub, and CloudHop
            pulls the spend updates GCP sends to it several times a day.
          </p>

          <ol class="steps">
            <li>
              <strong>Find the billing account</strong> of the project. Its ID looks like
              <code>0123AB-CDEF01-234567</code>.
              <CodeBlock :code="fill(findBillingAccount)" label="shell" />
            </li>
            <li>
              <strong>Create a topic and a pull subscription</strong> for the budget's updates. Create the subscription
              first: a topic keeps no messages, so updates sent before the subscription exists are lost.
              <CodeBlock :code="fill(createSubscription)" label="shell" />
            </li>
            <li>
              <strong>Create the budget</strong> in the Console, under <em>Billing → Budgets &amp; alerts → Create
              budget</em>:
              <ul>
                <li>
                  <strong>Scope:</strong> all projects and all services, since credits are shared by the whole billing
                  account. Under <em>Savings</em>, <strong>untick credits</strong>, so the budget counts the cost the
                  credits are paying for. With credits included, spend stays near zero until they run out.
                </li>
                <li>
                  <strong>Amount:</strong> <em>Specified amount</em>, your credit total, in the billing account's
                  currency.
                </li>
                <li>
                  <strong>Time range:</strong> <em>Custom</em>, from when the credits were granted to when they expire.
                  For a monthly spending limit instead, pick <em>Monthly</em>.
                </li>
                <li>
                  <strong>Actions:</strong> the thresholds you want to be emailed at, and tick <em>Connect a Pub/Sub
                  topic to this budget</em> with the <code>cloudhop-budget</code> topic.
                </li>
              </ul>
              <p>
                Or with <code>gcloud</code>, dates as <code>YYYY-MM-DD</code>. <code>CREDIT_TOTAL</code> is the amount
                followed by the <strong>billing account's currency</strong>, e.g. <code>300USD</code> or
                <code>7825800VND</code>; any other currency is rejected with <em>INVALID_ARGUMENT</em>. The Billing card
                on the account page shows the currency, or run
                <code>gcloud billing accounts describe BILLING_ACCOUNT_ID --format 'value(currencyCode)'</code>.
              </p>
              <CodeBlock :code="fill(createBudget)" label="shell" />
              <p>
                <strong>Keep the Pub/Sub topic.</strong> A budget without one only sends emails, and CloudHop never sees
                its spend. To connect a budget you already made (its ID is printed on creation, or listed by
                <code>gcloud billing budgets list</code>):
              </p>
              <CodeBlock :code="fill(connectBudget)" label="shell" />
              <p class="muted">
                This needs Billing Account Administrator or Billing Account Costs Manager on the billing account, and
                Pub/Sub Admin on the topic's project.
              </p>
            </li>
            <li>
              <strong>Grant CloudHop's service account access.</strong>
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
                      <td>
                        <code>roles/pubsub.subscriber</code><span class="badge warning">Required</span>
                        <div class="muted small">on the subscription</div>
                      </td>
                      <td>Pulls the spend updates. Without it, CloudHop sees no spend.</td>
                    </tr>
                    <tr>
                      <td>
                        <code>roles/billing.viewer</code><span class="badge plain">Recommended</span>
                        <div class="muted small">on the billing account</div>
                      </td>
                      <td>
                        Reads the billing account's name, and each budget's amount, period and the projects it covers.
                        Without it, budgets are known only from their updates.
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <CodeBlock :code="fill(grantBilling)" label="shell" />
            </li>
            <li>
              <strong>Connect it in CloudHop.</strong> On the account page, use <strong>Refresh billing</strong>, enter
              the subscription <code>projects/PROJECT_ID/subscriptions/cloudhop-budget</code> and save it, then refresh
              again. Spend shows once GCP has sent its first update, which can take a few hours.
            </li>
          </ol>

          <h3>Limits</h3>
          <ul>
            <li>
              <strong>The credit total is the budget amount you entered.</strong> The balance GCP actually holds is only
              shown on the billing account's overview page in the Console; update the budget if they differ.
            </li>
            <li>
              <strong>Spend lags.</strong> Usage reaches GCP's billing reports hours later, sometimes up to a day. Act
              on a margin, such as 85%, rather than waiting for 100%.
            </li>
          </ul>
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

.subsection {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 18px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: 6px;
  scroll-margin-top: 64px;
}

.subsection > h3 {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 0;
}

.subsection > h3 > svg {
  color: var(--primary);
}

.small {
  font-size: 12px;
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
