from accounts.models.account import Account
from common.cloud.errors import CloudAPIError
from common.cloud.gcp.client import GCPClient
from moves.adapters.base import BucketAdapter

# Bucket names hold no braces, so GCPClient's formatting leaves them alone
BUCKET_URL = "https://storage.googleapis.com/storage/v1/b/{name}?fields=name,location,projectNumber"
PROJECT_URL = "https://cloudresourcemanager.googleapis.com/v3/projects/{project_id}"
# Enough to read a bucket's metadata, granted on that bucket only
READER_ROLE = "roles/storage.legacyBucketReader"
# What Velero's GCP plugin does with backups in the bucket
BACKUP_PERMISSIONS = ("storage.objects.create", "storage.objects.get", "storage.objects.list", "storage.objects.delete")
# Answers which of the permissions the caller has; it needs no permission of its own
TEST_URL = "https://storage.googleapis.com/storage/v1/b/{name}/iam/testPermissions?" + "&".join(
    f"permissions={permission}" for permission in BACKUP_PERMISSIONS
)


class GCPBucketAdapter(BucketAdapter):
    provider = Account.Provider.GCP
    backup_role = "roles/storage.objectAdmin"

    def identity(self) -> str:
        return self.credential["client_email"]

    def get_bucket(self, name: str) -> dict | None:
        client = GCPClient(self.credential, self.account.project_id)
        try:
            bucket = client.get(BUCKET_URL.format(name=name))
        except CloudAPIError as exc:
            # Bucket names are global: Cloud Storage answers 404 for one nobody has, 403 for one we may not read
            if exc.status_code == 404:
                return None
            if exc.status_code == 403:
                email = self.identity()
                raise CloudAPIError(
                    f"gs://{name} exists, but {email} may not read it. Grant it: gcloud storage buckets "
                    f"add-iam-policy-binding gs://{name} --member serviceAccount:{email} --role {READER_ROLE}",
                    403,
                )
            raise

        # Buckets name their project by number; the account knows its project by ID
        number = client.get(PROJECT_URL)["name"].removeprefix("projects/")
        in_account = bucket.get("projectNumber") == number
        return {
            "name": bucket["name"],
            "location": bucket.get("location", "").lower(),
            "in_account": in_account,
            "owner": self.account.project_id if in_account else f"project number {bucket.get('projectNumber')}",
        }

    def missing_backup_access(self, name: str) -> list[str]:
        client = GCPClient(self.credential, self.account.project_id)
        granted = client.get(TEST_URL.format(name=name)).get("permissions", [])
        return [permission for permission in BACKUP_PERMISSIONS if permission not in granted]
