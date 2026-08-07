from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.transcription.provider_registry import (
    ProviderRegistry,
    ProviderResolutionFailure,
    ProviderResolutionReason,
    ProviderRuntimePorts,
)
from src.transcription.runtime_ports import MemoryInputSource, NeverCancel, NoOpProgressSink
from src.transcription.types import ContractValidationError


@dataclass(frozen=True)
class Provider:
    provider_key: str


@dataclass(frozen=True)
class Factory:
    provider_key: str
    returned_key: str | None = None
    raises: bool = False

    def create(self, ports):
        if self.raises:
            raise RuntimeError("private factory failure")
        return Provider(self.returned_key or self.provider_key)


PORTS = ProviderRuntimePorts(
    "11111111-1111-4111-8111-111111111111",
    MemoryInputSource(b"x"), NoOpProgressSink(), NeverCancel()
)


def test_runtime_ports_require_application_job_identity():
    with pytest.raises(ContractValidationError):
        ProviderRuntimePorts(
            "not-a-job-id", MemoryInputSource(b"x"), NoOpProgressSink(), NeverCancel()
        )


def test_registry_resolves_without_provider_name_branch():
    registry = ProviderRegistry(
        (
            Factory("fourth-provider"),
            Factory("funasr-sensevoice"),
        )
    )
    resolved = registry.resolve("fourth-provider", PORTS)
    assert resolved.provider_key == "fourth-provider"


def test_registry_unknown_is_finite():
    result = ProviderRegistry(()).resolve("unknown-provider", PORTS)
    assert type(result) is ProviderResolutionFailure
    assert result.reason_code is ProviderResolutionReason.unknown_provider


def test_registry_rejects_duplicates_and_unsorted_factories():
    with pytest.raises(ContractValidationError):
        ProviderRegistry((Factory("same-provider"), Factory("same-provider")))
    with pytest.raises(ContractValidationError):
        ProviderRegistry((Factory("z-provider"), Factory("a-provider")))


@pytest.mark.parametrize(
    ("factory", "reason"),
    [
        (
            Factory("expected-provider", returned_key="wrong-provider"),
            ProviderResolutionReason.provider_key_mismatch,
        ),
        (
            Factory("expected-provider", raises=True),
            ProviderResolutionReason.provider_factory_failed,
        ),
    ],
)
def test_registry_factory_failures_are_finite(factory, reason):
    result = ProviderRegistry((factory,)).resolve("expected-provider", PORTS)
    assert type(result) is ProviderResolutionFailure
    assert result.reason_code is reason
