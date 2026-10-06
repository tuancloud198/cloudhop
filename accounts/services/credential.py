import uuid
from pathlib import Path

from django.conf import settings

from common.cloud import InvalidCredential

__all__ = ["delete_credential_file", "store_credential_file"]

# A service account key is about 2 KB
MAX_CREDENTIAL_SIZE = 64 * 1024


def store_credential_file(upload) -> str:
    """Save an uploaded credential file under CREDENTIALS_DIR and return its absolute path.

    Raises InvalidCredential if the file is too large.
    """
    if upload.size > MAX_CREDENTIAL_SIZE:
        raise InvalidCredential(f"credential file is larger than {MAX_CREDENTIAL_SIZE // 1024} KB")

    directory = Path(settings.CREDENTIALS_DIR)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.json"
    # Created owner-only, since the file holds a private key
    with path.open("xb") as file:
        path.chmod(0o600)
        for chunk in upload.chunks():
            file.write(chunk)
    return str(path)


def delete_credential_file(path: str) -> None:
    """Delete a credential file if it was stored by store_credential_file; leave any other path alone."""
    file = Path(path).resolve()
    if file.parent == Path(settings.CREDENTIALS_DIR).resolve():
        file.unlink(missing_ok=True)
