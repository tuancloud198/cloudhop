# CloudHop

CloudHop keeps track of Kubernetes workloads spread over several cloud accounts, and of how much of each account's budget is spent, so the workloads can be mirrored to another account when one runs low.

It runs on your machine only. It reads from the cloud and never changes anything in a project or a cluster.

## What it does

- **Accounts:** connect a cloud account with a service account key. Each account is one cloud project. The key is checked against the project and saved locally in `credentials/`.
- **Clusters:** list the account's Kubernetes clusters in every location, with their version, node pools and machine sizes.
- **Resources:** read every built-in kind from a cluster and keep the objects people created, as clean manifests that can be applied to another cluster:
  - Objects the cluster or provider makes for itself are left out: system namespaces, objects owned by another object, built-in RBAC, defaults.
  - Fields set by the API server are removed, and `status` is kept apart from the manifest.
  - Secrets keep their values. The UI can decode them.
- **Billing:** find the billing account paying for each account, read its budgets, and follow spend through the budget notifications the provider sends to Pub/Sub. Several accounts can share one billing account and its credits. `billing.services.spend_status(account)` tells how much of the budget covering an account is used.
- **Moves** *(planned)*: move workloads to another account when its budget is close to used up.

Only **Google Cloud** (GKE) is supported for now. AWS and Azure are planned.

## How it is built

| Part | What it is |
|---|---|
| `accounts/` | Cloud accounts and their credentials |
| `clusters/` | Kubernetes clusters of each account |
| `kubernetes/` | Resources stored from each cluster |
| `billing/` | Billing accounts, budgets and spend updates |
| `moves/` | Migration tasks (not built yet) |
| `common/` | Shared code: cloud errors, the provider adapter registry, the GCP and Kubernetes clients |
| `frontend/` | Vue 3 + Vite UI |

The backend is Django with Django REST Framework, serving `/api/v1/`, with SQLite for storage. Each app reaches the provider through its own adapters in `<app>/adapters/`, one per provider. See [AGENTS.md](AGENTS.md) for the code layout rules.

## Running it

You need Python 3.12 or later and Node.js.

```sh
# Backend, on :8000
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver

# Frontend, on :5173, in another shell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite forwards `/api` to Django; set `CLOUDHOP_API` to point it elsewhere.

Run the tests with `python manage.py test`.

## Setting up a cloud account

The **Guide** page in the app (`/guide`) has the full steps for each provider. For Google Cloud they are:

1. Enable the APIs and create a service account with `roles/container.viewer`.
2. Bind it to a ClusterRole that can read Secrets in each cluster, because Kubernetes Engine Viewer cannot.
3. Optionally, track spend: create a budget whose amount is your credit total, connect it to a Pub/Sub topic, and give CloudHop's service account `roles/pubsub.subscriber` on the subscription.
4. Add the key in CloudHop, then sync clusters, resources and billing from the UI.

## Security

CloudHop is meant to run locally for one user, and it has no login.

- `credentials/` holds service account keys.
- `db.sqlite3` holds every synced manifest, including Secret values.

Both are git-ignored. Keep them on your machine.
