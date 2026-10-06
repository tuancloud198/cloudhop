import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from common.cloud import InvalidCredential

from .adapters import GCPCredentialAdapter
from .models import Account


def key_file(client_id="1234", project_id="demo-project", name="key.json"):
    content = {"type": "service_account", "client_id": client_id, "project_id": project_id}
    return SimpleUploadedFile(name, json.dumps(content).encode(), content_type="application/json")


class AccountUploadTests(APITestCase):
    def setUp(self):
        self.credentials_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.credentials_dir.cleanup)
        settings_patch = override_settings(CREDENTIALS_DIR=Path(self.credentials_dir.name))
        settings_patch.enable()
        self.addCleanup(settings_patch.disable)
        # validate() calls GCP; the tests only cover what happens around it
        validate_patch = mock.patch.object(GCPCredentialAdapter, "validate")
        self.validate = validate_patch.start()
        self.addCleanup(validate_patch.stop)

    def stored_files(self):
        return list(Path(self.credentials_dir.name).glob("*.json"))

    def upload(self, **files):
        return self.client.post(
            "/api/v1/accounts/",
            {"name": "demo", "provider": "gcp", **files},
            format="multipart",
        )

    def test_upload_stores_file_and_creates_account(self):
        response = self.upload(credential_file=key_file())

        self.assertEqual(response.status_code, 201, response.data)
        [stored] = self.stored_files()
        account = Account.objects.get()
        self.assertEqual(account.credential_ref, str(stored))
        self.assertEqual((account.external_id, account.project_id, account.is_valid), ("1234", "demo-project", True))
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("credential_file", response.data)

    def test_rejected_credential_removes_stored_file(self):
        self.validate.side_effect = InvalidCredential("permission denied")

        response = self.upload(credential_file=key_file())

        self.assertEqual(response.status_code, 400)
        self.assertIn("permission denied", str(response.data["credential_file"]))
        self.assertEqual(self.stored_files(), [])
        self.assertFalse(Account.objects.exists())

    def test_duplicate_credential_removes_stored_file(self):
        self.assertEqual(self.upload(credential_file=key_file()).status_code, 201)

        response = self.upload(credential_file=key_file())

        self.assertEqual(response.status_code, 400)
        self.assertIn("already exists", str(response.data["credential_file"]))
        self.assertEqual(len(self.stored_files()), 1)

    def test_not_a_service_account_key(self):
        bad = SimpleUploadedFile("key.json", b'{"type": "authorized_user"}')

        response = self.upload(credential_file=bad)

        self.assertEqual(response.status_code, 400)
        self.assertIn("not a GCP service account key", str(response.data["credential_file"]))
        self.assertEqual(self.stored_files(), [])

    def test_requires_file_or_ref(self):
        response = self.upload()

        self.assertEqual(response.status_code, 400)
        self.assertIn("credential_file", response.data)

    def test_delete_account_removes_stored_file(self):
        account_id = self.upload(credential_file=key_file()).data["id"]

        response = self.client.delete(f"/api/v1/accounts/{account_id}/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.stored_files(), [])

    def test_replacing_credential_removes_old_file(self):
        account_id = self.upload(credential_file=key_file()).data["id"]
        [old] = self.stored_files()

        response = self.client.patch(
            f"/api/v1/accounts/{account_id}/",
            {"credential_file": key_file(client_id="5678")},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200, response.data)
        [new] = self.stored_files()
        self.assertNotEqual(new, old)
        self.assertEqual(Account.objects.get().external_id, "5678")

    def test_external_credential_ref_is_never_deleted(self):
        outside = Path(tempfile.mkdtemp()) / "key.json"
        outside.write_text(json.dumps({"type": "service_account", "client_id": "9", "project_id": "p"}))
        self.addCleanup(outside.unlink, missing_ok=True)
        account_id = self.client.post(
            "/api/v1/accounts/", {"name": "ref", "provider": "gcp", "credential_ref": str(outside)}
        ).data["id"]

        self.client.delete(f"/api/v1/accounts/{account_id}/")

        self.assertTrue(outside.exists())
