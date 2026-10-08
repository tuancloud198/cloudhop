# AGENTS.md

## App layout

Inside each Django app, `models`, `views`, `services` and `serializers` are packages (folders), not single `.py` files. Put each resource in its own module, and have the package's `__init__.py` re-export the public names.

```
<app>/
├── models/
│   ├── __init__.py      # from .account import Account
│   └── account.py
├── views/
│   ├── __init__.py      # from .account import AccountViewSet
│   └── account.py
├── services/
│   ├── __init__.py      # from .account import InvalidCredential, resolve_account
│   └── account.py
└── serializers/
    ├── __init__.py      # from .account import AccountSerializer
    └── account.py
```

Rules:

- Name each module after the resource it holds (`account.py`, `cluster.py`, ...).
- Re-export every public name in the package `__init__.py` and list it in `__all__`. Other code imports from the package (`from accounts.models import Account`), never from the submodule.
- Use absolute imports that name the app (`from accounts.models import Account`), never relative ones (`from ..models import Account`). The only exception is the re-exports in a package's `__init__.py` (`from .account import Account`).
- To import another module of the same package, name the module (`from billing.adapters.base import BillingAdapter`). Importing the package itself would be circular, since its `__init__.py` imports that module.
- Every model must be re-exported from `models/__init__.py`, otherwise Django will not detect it for migrations.
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
  - `__init__.py` imports every provider module, otherwise the adapter is never registered.
- Look up an adapter with `<Base>Adapter.for_account(account)`. A provider with no adapter raises `UnsupportedProvider`.
- Do not add a method for one app's job to another app's adapter. A new job (e.g. listing nodes) gets its own adapter base in the app that needs it.

## Frontend: `frontend/`

Vue 3 + Vite + vue-router, plain CSS, no UI library. Colors are tokens in `src/styles.css`, each written as `light-dark(light, dark)`; never hard-code a color in a component. The theme (system / light / dark) is kept by `src/theme.js`.

- `src/api.js` is the only place that calls the backend (`/api/v1/...`). Add a method there for each new endpoint; it sends the CSRF token and turns DRF errors into `ApiError` (`message`, `status`, `fields`).
- `src/views/` holds one component per route (`router.js`); `src/components/` holds reusable pieces.
- Shared state is a plain `reactive` object (`src/accounts.js`); notifications go through `notify()` in `src/toasts.js`.
- In development, run Django on :8000 and `npm run dev` in `frontend/`; Vite proxies `/api` to Django (`CLOUDHOP_API` overrides the target). `CSRF_TRUSTED_ORIGINS` in settings allows the Vite origin.
