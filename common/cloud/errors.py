class CloudAPIError(Exception):
    """A call to the cloud provider failed; the message says why and is safe to show to the user."""

    def __init__(self, message: str = "", status_code: int | None = None):
        super().__init__(message)
        # The provider's HTTP status, when it answered (e.g. 404 for something that does not exist)
        self.status_code = status_code


class CloudTimeout(CloudAPIError):
    """The provider did not answer in time."""


class InvalidCredential(CloudAPIError):
    """The credential cannot be used; the message says why and is safe to show to the user."""


class UnsupportedProvider(CloudAPIError):
    """No adapter is registered for the provider."""
