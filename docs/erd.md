# Data model

CloudHop's tables in PostgreSQL, by app. Names are the real table and column names, so they can be used in SQL as they are. Django's own tables (auth, sessions, admin) are left out.

```mermaid
erDiagram
    accounts_account ||--o{ clusters_clusters : "has"
    accounts_account ||--o| billing_accountbilling : "is paid for by"
    billing_billingaccount |o--o{ billing_accountbilling : "pays for"
    billing_billingaccount ||--o{ billing_budget : "has"
    billing_budget ||--o{ billing_budgetstatus : "reports spend in"
    clusters_clusters ||--o{ kubernetes_kuberesource : "stores"
    clusters_clusters ||--o{ moves_move : "source of"
    clusters_clusters ||--o{ moves_move : "target of"
    moves_move ||--o{ moves_moveevent : "logs"

    accounts_account {
        bigint id PK
        varchar provider UK "gcp, aws, azure; unique with external_id"
        varchar external_id UK "GCP: service account unique ID"
        varchar name
        varchar project_id "GCP project"
        varchar credential_ref "path of the key file"
        boolean is_active
        boolean is_valid "credential checked against the provider"
        timestamptz added_at
        timestamptz updated_at
    }

    clusters_clusters {
        bigint id PK
        bigint account_id_id FK "accounts_account; unique with external_id"
        varchar external_id UK "GKE: projects/.../locations/.../clusters/..."
        varchar name
        varchar location
        varchar status "RUNNING, ..."
        varchar kubernetes_version "nullable"
        jsonb spec "endpoint, network, node pools, ..."
        boolean is_active "false once the provider stops returning it"
        timestamptz created_at
        timestamptz updated_at
    }

    kubernetes_kuberesource {
        bigint id PK
        bigint cluster_id FK "clusters_clusters"
        varchar group UK "unique: cluster, group, kind, namespace, name"
        varchar version
        varchar kind UK
        varchar namespace UK "empty for cluster-scoped objects"
        varchar name UK
        varchar uid
        jsonb manifest "cleaned, ready to apply elsewhere"
        jsonb status
        timestamptz kube_created_at "nullable"
        timestamptz created_at
    }

    billing_billingaccount {
        bigint id PK
        varchar provider UK "unique with external_id"
        varchar external_id UK "GCP: 012345-ABCDEF-678901"
        varchar name
        varchar currency
        boolean is_open
        varchar pubsub_subscription "where budget notifications are pulled from"
        timestamptz created_at
        timestamptz updated_at
    }

    billing_accountbilling {
        bigint id PK
        bigint account_id FK,UK "accounts_account, one per account"
        bigint billing_account_id FK "billing_billingaccount; null when billing is off"
        boolean billing_enabled
        varchar project_number "matched against budget projects"
        timestamptz synced_at
    }

    billing_budget {
        bigint id PK
        bigint billing_account_id FK "billing_billingaccount; unique with external_id"
        varchar external_id UK "GCP budget ID"
        varchar name
        numeric amount "nullable: follows last period's spend"
        varchar currency
        varchar period "month, quarter, year, custom"
        date start_date "nullable, custom periods"
        date end_date "nullable"
        varchar credit_treatment
        jsonb projects "project numbers; empty means every project"
        boolean has_other_filters
        varchar pubsub_topic
        boolean is_active "false once the budget API stops returning it"
        timestamptz created_at
        timestamptz updated_at
    }

    billing_budgetstatus {
        bigint id PK
        bigint budget_id FK "billing_budget"
        varchar message_id UK "Pub/Sub message; stores each notification once"
        numeric cost_amount "spend so far in the budget period"
        numeric budget_amount
        varchar currency
        timestamptz interval_start "start of the budget period"
        double threshold_exceeded "nullable"
        double forecast_threshold_exceeded "nullable"
        timestamptz published_at "the latest is the current spend"
        timestamptz created_at
    }

    moves_move {
        bigint id PK
        bigint source_cluster_id FK "clusters_clusters"
        bigint target_cluster_id FK "clusters_clusters; never the source"
        jsonb namespaces
        varchar method "velero, manifests"
        varchar mode "cutover, copy (source keeps running)"
        jsonb storage_class_mapping "Velero: source class to target class"
        varchar storage_location "Velero BackupStorageLocation"
        varchar status "pending ... done, failed, cancelled"
        text waiting_on
        varchar failed_step
        text error
        integer attempt "part of the Velero object names"
        varchar backup_name
        varchar restore_name
        jsonb replicas "namespace/Kind/name to replicas before the cutover"
        timestamptz step_started_at
        timestamptz checked_at
        timestamptz finished_at
        timestamptz created_at
        timestamptz updated_at
    }

    moves_moveevent {
        bigint id PK
        bigint move_id FK "moves_move"
        varchar step
        varchar level "info, warning, error"
        text message
        timestamptz created_at
    }
```

## Notes

- **Nothing is hard-deleted by a sync.** Clusters and budgets the provider no longer returns get `is_active = false`; resources are replaced as a whole on each resource sync of a cluster. Deleting an account deletes its clusters, their resources and moves (`ON DELETE CASCADE`).
- **Several accounts can share a billing account** (`billing_accountbilling.billing_account_id`), and with it its budgets and credits. A budget covers an account when its `projects` list is empty or holds the account's `project_number` (or project ID). That match is made in code, not by a foreign key.
- **Current spend** is the newest `billing_budgetstatus` row of a covering budget, by `published_at` (indexed on `budget_id, published_at DESC`). `cost_amount` is already the period's running total; rows are not summed.
- **`clusters_clusters.account_id_id`:** the foreign key field on the model is named `account_id`, so Django names its column `account_id_id`.
- **Indexes** besides primary keys, foreign keys and the unique constraints above: `kubernetes_kuberesource (cluster_id, kind)` and `(cluster_id, namespace)`.
- **`moves_move`** has a check constraint `source_cluster_id <> target_cluster_id`. Only one move per cluster runs at a time; that is checked in code.
