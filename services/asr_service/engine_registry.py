"""Service-side profile-to-engine registry."""
from __future__ import annotations

from dataclasses import dataclass

from src.transcription.types import ContractValidationError, validate_profile_id

from .engine_protocol import AsrEngine, ServiceProfileConfig


@dataclass(frozen=True, slots=True)
class EngineRegistration:
    engine: AsrEngine
    config: ServiceProfileConfig

    def __post_init__(self) -> None:
        if type(self.config) is not ServiceProfileConfig:
            raise ContractValidationError("invalid_service_profile_config", "config")
        if (
            self.engine.provider_key != self.config.provider_key
            or self.engine.service_profile_id != self.config.service_profile_id
        ):
            raise ContractValidationError("engine_profile_mismatch", "registration")


@dataclass(frozen=True, slots=True)
class EngineRegistry:
    registrations: tuple[EngineRegistration, ...]

    def __post_init__(self) -> None:
        if type(self.registrations) is not tuple:
            raise ContractValidationError(
                "mutable_collection", "registrations"
            )
        keys = []
        for registration in self.registrations:
            if type(registration) is not EngineRegistration:
                raise ContractValidationError(
                    "invalid_engine_registration", "registrations"
                )
            validate_profile_id(
                registration.config.service_profile_id, "service_profile_id"
            )
            keys.append(registration.config.service_profile_id)
        if keys != sorted(set(keys)):
            raise ContractValidationError("engines_not_sorted_unique", "engines")

    def resolve(self, service_profile_id: str) -> EngineRegistration | None:
        validate_profile_id(service_profile_id, "service_profile_id")
        return next(
            (
                item
                for item in self.registrations
                if item.config.service_profile_id == service_profile_id
            ),
            None,
        )

    def available_profile_ids(self) -> tuple[str, ...]:
        available: list[str] = []
        for registration in self.registrations:
            try:
                engine = registration.engine
                capabilities = engine.capabilities()
                if (
                    capabilities.provider_key == registration.config.provider_key
                    and capabilities.service_profile_id
                    == registration.config.service_profile_id
                    and capabilities.available
                ):
                    available.append(registration.config.service_profile_id)
            except Exception:
                continue
        return tuple(available)
