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

The backend is Django with Django REST Framework, serving `/api/v1/`, with PostgreSQL for storage and Celery (with Redis) for background syncs. Each app reaches the provider through its own adapters in `<app>/adapters/`, one per provider. See [AGENTS.md](AGENTS.md) for the code layout rules.

## Running it

### With Docker Compose

```sh
docker compose up --build -d
```

Open http://localhost:8080. This starts:

| Service | What it runs |
|---|---|
| `fe` | The built UI on nginx, which forwards `/api`, `/admin` and `/static` to `be` |
| `be` | Django on gunicorn |
| `migrate` | `manage.py migrate`, once, before `be` and Celery start |
| `celery-worker` | Runs the syncs |
| `celery-beat` | Queues the syncs every 15 minutes |
| `postgres`, `redis` | The database and Celery's broker, also published on localhost:5432 and :6379 |

`http_proxy`, `https_proxy` and `no_proxy` are taken from your shell, both for building the images and for the backend's calls to the cloud. Other settings (`DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `FE_PORT`, ...) can be set in a `.env` file next to `compose.yaml`. Uploaded keys and the database live in the `credentials` and `postgres` volumes; `docker compose down -v` deletes them.

### For development

You need Python 3.12 or later, Node.js, and PostgreSQL and Redis. The compose ones work:

```sh
docker compose up -d postgres redis

# Backend, on :8000
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver

# Frontend, on :5173, in another shell
cd frontend
npm install
npm run dev

# Background syncs, in another shell
celery -A cloudhop worker --beat --concurrency 2 --loglevel info
```

Open http://localhost:5173. Vite forwards `/api` to Django; set `CLOUDHOP_API` to point it elsewhere. Django reads the database from `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD`, and Celery's broker from `CELERY_BROKER_URL`; the defaults match the compose services.

Run the tests with `python manage.py test`. They need PostgreSQL too.

### Background syncs

The Celery worker syncs every account's clusters and billing, and every cluster's resources, each 15 minutes (`SYNC_INTERVAL_MINUTES` in settings). The refresh buttons in the UI still sync right away. A sync that fails is logged by the worker and tried again on the next run.

## Setting up a cloud account

The **Guide** page in the app (`/guide`) has the full steps for each provider. For Google Cloud they are:

1. Enable the APIs and create a service account with `roles/container.viewer`.
2. Bind it to a ClusterRole that can read Secrets in each cluster, because Kubernetes Engine Viewer cannot.
3. Optionally, track spend: create a budget whose amount is your credit total, connect it to a Pub/Sub topic, and give CloudHop's service account `roles/pubsub.subscriber` on the subscription.
4. Add the key in CloudHop, then sync clusters, resources and billing from the UI. After that the worker keeps them up to date.

## Security

CloudHop is meant to run locally for one user, and it has no login.

- `credentials/` (or the `credentials` volume with compose) holds service account keys.
- The database holds every synced manifest, including Secret values.

Keep both on your machine. Every port compose publishes is bound to localhost only.
