import json
from pathlib import Path

from .errors import InvalidCredential


def load_credential_file(path: str) -> dict:
    """Read a JSON credential file, raising InvalidCredential with the reason if it cannot be used."""
    file = Path(path)
    if not file.is_file():
        raise InvalidCredential(f"credential file not found: {file}")
    try:
        return json.loads(file.read_text())
    except OSError as exc:
        raise InvalidCredential(f"cannot read credential file: {exc.strerror}")
    except json.JSONDecodeError:
        raise InvalidCredential("credential file is not valid JSON")
