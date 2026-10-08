# AGENTS.md

## App layout

Inside each Django app, `models`, `views`, `services` and `serializers` are packages (folders), not single `.py` files. Put each resource in its own module and import names directly from that module. Do not re-export names or declare `__all__`.

```
<app>/
├── models/
│   ├── __init__.py      # empty
│   └── account.py
├── views/
│   ├── __init__.py      # empty
│   └── account.py
├── services/
│   ├── __init__.py      # empty
│   └── account.py
└── serializers/
    ├── __init__.py      # empty
    └── account.py
```

Rules:

- Name each module after the resource it holds (`account.py`, `cluster.py`, ...).
- Import public names from their defining module (`from accounts.models.account import Account`), never through package re-exports.
- Use absolute imports that name the app, never relative imports, including in `__init__.py`.
- To import another module of the same package, name the module (`from billing.adapters.base import BillingAdapter`).
- Keep `models/__init__.py` empty and import model classes directly from their defining modules at the places that use them. Do not add model-loading overrides to `AppConfig`.
- After adding or moving a model, run `python manage.py makemigrations --check --dry-run` to confirm Django still sees it.

`accounts/` is the reference implementation.

## Shared code: `common/`

`common/` holds code shared by several apps. It is a plain Python package, not a Django app.

- Never add it to `INSTALLED_APPS`, and never give it `apps.py`, models, migrations, admin, views or URLs. If shared code needs a database table, it belongs in an app instead.
- Imports go one way: apps import from `common`, and `common` never imports from an app. Pass plain data (dicts, strings) into `common` instead of model instances.

## Cloud providers

Provider code is split by job:

- `common/cloud/` has what every job needs: errors (`CloudAPIError`, `InvalidCredential`, `UnsupportedProvider`), `load_credential_file()`, the `ProviderAdapter` registry base, and one client per provider (`common/cloud/gcp/client.py`: `GCPClient` handles login, requests and error messages).
- Each app owns the adapters for its own job in `<app>/adapters/`:
  - `base.py` defines the app's adapter base as a direct subclass of `ProviderAdapter` (e.g. `CredentialAdapter`, `ClusterAdapter`). That gives it its own registry.
  - `<provider>.py` defines one concrete adapter per provider. Setting `provider = Account.Provider.X` registers it automatically.
  - `__init__.py` loads every provider module with an absolute module import (`import accounts.adapters.gcp`), otherwise the adapter is never registered. Do not re-export adapter classes.
- Look up an adapter with `<Base>Adapter.for_account(account)`. A provider with no adapter raises `UnsupportedProvider`.
- Do not add a method for one app's job to another app's adapter. A new job (e.g. listing nodes) gets its own adapter base in the app that needs it.

## Frontend: `frontend/`

Vue 3 + Vite + vue-router, plain CSS, no UI library. Colors are tokens in `src/styles.css`, each written as `light-dark(light, dark)`; never hard-code a color in a component. The theme (system / light / dark) is kept by `src/theme.js`.

- Use absolute `/src/...` paths for local imports, including components, styles and assets. Import directly from the defining module; do not add re-export files.
- `src/api.js` is the only place that calls the backend (`/api/v1/...`). Add a method there for each new endpoint; it sends the CSRF token and turns DRF errors into `ApiError` (`message`, `status`, `fields`).
- `src/views/` holds one component per route (`router.js`); `src/components/` holds reusable pieces.
- Shared state is a plain `reactive` object (`src/accounts.js`); notifications go through `notify()` in `src/toasts.js`.
- In development, start PostgreSQL and Redis with `docker compose up -d postgres redis`, then run Django on :8000 and `npm run dev` in `frontend/`; Vite proxies `/api` to Django (`CLOUDHOP_API` overrides the target). `CSRF_TRUSTED_ORIGINS` in settings allows the Vite origin.
