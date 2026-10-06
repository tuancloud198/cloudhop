class CloudAPIError(Exception):
    """A call to the cloud provider failed; the message says why and is safe to show to the user."""


class InvalidCredential(CloudAPIError):
    """The credential cannot be used; the message says why and is safe to show to the user."""


class UnsupportedProvider(CloudAPIError):
    """No adapter is registered for the provider."""
