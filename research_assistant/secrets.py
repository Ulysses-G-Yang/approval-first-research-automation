from __future__ import annotations

from typing import Dict, Protocol


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, reference: str) -> str:
        ...

    def set(self, reference: str, value: str) -> None:
        ...


class KeyringSecretStore:
    service_name = "generic-crawler"

    def __init__(self, service_name: str | None = None):
        if service_name:
            self.service_name = service_name

    @staticmethod
    def _keyring():
        try:
            import keyring  # type: ignore[import]
        except Exception as exc:  # pragma: no cover
            raise SecretStoreError("keyring is required to store API keys securely.") from exc
        return keyring

    def get(self, reference: str) -> str:
        value = self._keyring().get_password(self.service_name, reference)
        if not value:
            raise SecretStoreError(f"No API key stored for secret_ref={reference!r}.")
        return value

    def set(self, reference: str, value: str) -> None:
        clean = value.strip()
        if not clean:
            raise SecretStoreError("API key cannot be empty.")
        self._keyring().set_password(self.service_name, reference, clean)


class InMemorySecretStore:
    """Test-only store that never persists credentials."""

    def __init__(self, values: Dict[str, str] | None = None):
        self.values = dict(values or {})

    def get(self, reference: str) -> str:
        if reference not in self.values:
            raise SecretStoreError(f"No API key stored for secret_ref={reference!r}.")
        return self.values[reference]

    def set(self, reference: str, value: str) -> None:
        self.values[reference] = value
