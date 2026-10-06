from abc import ABC

from .errors import UnsupportedProvider


class ProviderAdapter(ABC):
    """Base for per-provider adapters.

    A direct subclass (e.g. CredentialAdapter) gets its own registry. Its subclasses
    that set `provider` (e.g. GCPCredentialAdapter) register into it automatically.
    """

    provider: str
    _registry: dict[str, type["ProviderAdapter"]]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "provider" in cls.__dict__:
            cls._registry[cls.provider] = cls
        else:
            cls._registry = {}

    @classmethod
    def for_provider(cls, provider: str) -> type["ProviderAdapter"]:
        try:
            return cls._registry[provider]
        except KeyError:
            raise UnsupportedProvider(f"provider '{provider}' is not supported yet")
