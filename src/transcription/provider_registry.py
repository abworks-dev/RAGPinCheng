"""Engine-neutral Provider factory registry."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .provider_protocol import TranscriptionProvider
from .runtime_ports import CancellationProbe, ProviderProgressSink, TranscriptionInputSource
from .types import ContractValidationError, require_exact_enum, validate_provider_key, validate_uuid


@dataclass(frozen=True, slots=True)
class ProviderRuntimePorts:
    application_job_id: str
    input_source: TranscriptionInputSource
    progress_sink: ProviderProgressSink
    cancellation_probe: CancellationProbe

    def __post_init__(self) -> None:
        validate_uuid(self.application_job_id, "application_job_id")


class ProviderFactory(Protocol):
    @property
    def provider_key(self) -> str: ...

    def create(self, ports: ProviderRuntimePorts) -> TranscriptionProvider: ...


class ProviderResolutionReason(Enum):
    unknown_provider = "unknown_provider"
    provider_factory_failed = "provider_factory_failed"
    provider_key_mismatch = "provider_key_mismatch"


@dataclass(frozen=True, slots=True)
class ProviderResolutionFailure:
    provider_key: str
    reason_code: ProviderResolutionReason

    def __post_init__(self) -> None:
        validate_provider_key(self.provider_key)
        require_exact_enum(self.reason_code, ProviderResolutionReason, "reason_code")


@dataclass(frozen=True, slots=True)
class ProviderRegistry:
    factories: tuple[ProviderFactory, ...]

    def __post_init__(self) -> None:
        if type(self.factories) is not tuple:
            raise ContractValidationError("mutable_collection", "factories")
        keys: list[str] = []
        for factory in self.factories:
            key = factory.provider_key
            validate_provider_key(key)
            keys.append(key)
        if keys != sorted(set(keys)):
            raise ContractValidationError("providers_not_sorted_unique", "factories")

    def resolve(
        self, provider_key: str, ports: ProviderRuntimePorts
    ) -> TranscriptionProvider | ProviderResolutionFailure:
        validate_provider_key(provider_key)
        factory = next((item for item in self.factories if item.provider_key == provider_key), None)
        if factory is None:
            return ProviderResolutionFailure(
                provider_key, ProviderResolutionReason.unknown_provider
            )
        try:
            provider = factory.create(ports)
        except Exception:
            return ProviderResolutionFailure(
                provider_key, ProviderResolutionReason.provider_factory_failed
            )
        try:
            if provider.provider_key != provider_key:
                return ProviderResolutionFailure(
                    provider_key, ProviderResolutionReason.provider_key_mismatch
                )
        except Exception:
            return ProviderResolutionFailure(
                provider_key, ProviderResolutionReason.provider_factory_failed
            )
        return provider
