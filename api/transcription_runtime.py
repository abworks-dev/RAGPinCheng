"""Application adapters injected into remote transcription Providers."""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from threading import Event
from typing import Callable

from src.transcription.asr_service_contract import ServiceCapabilities
from src.transcription.persistence import CHECKPOINT_SCHEMA_VERSION, TranscriptionCheckpoint
from src.transcription.profile import (
    FasterWhisperRemoteConfig,
    ProfileRegistry,
    Qwen3AsrRemoteConfig,
    RemoteAsrServiceConfig,
    TranscriptionProfileDefinition,
    WhisperXRemoteConfig,
)
from src.transcription.profile_catalog import build_phase3_profile_catalog
from src.transcription.provider_registry import ProviderFactory, ProviderRuntimePorts
from src.transcription.remote_provider import HttpxAsrServiceClient, RemoteAsrProvider
from src.transcription.runtime_ports import CancellationProbe, ProviderRuntimeState
from src.transcription.types import (
    ContractValidationError,
    TranscriptionJobStage,
    TranscriptionJobStatus,
    require_exact_enum,
    require_int,
    validate_provider_key,
    validate_uuid,
)

from .db import connect
from .transcription_store import SQLiteTranscriptionStore, StoreConflictError


def build_phase4_profile_registry(
    *, upload_part_bytes: int, poll_interval_ms: int, expected_api_version: str
) -> ProfileRegistry:
    profiles: list[TranscriptionProfileDefinition] = []
    for entry in build_phase3_profile_catalog():
        base = entry.profile
        if type(base.provider_config) not in (
            RemoteAsrServiceConfig,
            FasterWhisperRemoteConfig,
            Qwen3AsrRemoteConfig,
            WhisperXRemoteConfig,
        ):
            raise ContractValidationError(
                "invalid_provider_config", "provider_config"
            )
        provider_config = replace(
            base.provider_config,
            upload_part_bytes=upload_part_bytes,
            poll_interval_ms=poll_interval_ms,
            expected_api_version=expected_api_version,
        )
        profiles.append(
            TranscriptionProfileDefinition.create(
                profile_id=base.profile_id,
                display_name=base.display_name,
                description=base.description,
                provider_key=base.provider_key,
                provider_config=provider_config,
                normalizer_config=base.normalizer_config,
                qualification=base.qualification,
                admission=base.admission,
                release_policy=base.release_policy,
                profile_definition_version=base.profile_definition_version,
                provider_adapter_version=base.provider_adapter_version,
                canonical_schema_version=base.canonical_schema_version,
                normalizer_version=base.normalizer_version,
                formatter_version=base.formatter_version,
                evidence_refs=base.evidence_refs,
            )
        )
    return ProfileRegistry(tuple(profiles))


@dataclass(frozen=True, slots=True)
class RemoteAsrProviderFactory:
    base_url: str
    token: str
    connect_timeout_seconds: float
    request_timeout_seconds: float
    provider_key: str

    def __post_init__(self) -> None:
        validate_provider_key(self.provider_key)

    def create(self, ports: ProviderRuntimePorts) -> RemoteAsrProvider:
        return RemoteAsrProvider(
            HttpxAsrServiceClient(
                self.base_url,
                self.token,
                self.connect_timeout_seconds,
                self.request_timeout_seconds,
            ),
            ports,
            self.provider_key,
        )

    def capabilities(self) -> ServiceCapabilities:
        return HttpxAsrServiceClient(
            self.base_url,
            self.token,
            self.connect_timeout_seconds,
            self.request_timeout_seconds,
        ).capabilities()


@dataclass(frozen=True, slots=True)
class StoreProgressSink:
    job_id: str
    connect_factory: Callable = connect
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        validate_uuid(self.job_id, "job_id")

    def record(
        self,
        service_job_id: str,
        processed_ms: int,
        total_ms: int,
        state: ProviderRuntimeState,
        reason_code: str | None,
    ) -> None:
        validate_uuid(service_job_id, "service_job_id")
        require_int(processed_ms, "processed_ms")
        require_int(total_ms, "total_ms", positive=True)
        require_exact_enum(state, ProviderRuntimeState, "state")
        if reason_code is not None and type(reason_code) is not str:
            raise ContractValidationError("invalid_string", "reason_code")
        conn = self.connect_factory()
        try:
            store = SQLiteTranscriptionStore(conn)
            for _attempt in range(2):
                job = store.load_job(self.job_id)
                if job.status is TranscriptionJobStatus.cancelled:
                    return
                if job.status is not TranscriptionJobStatus.running:
                    raise ContractValidationError("progress_requires_running", "job.status")
                if total_ms != job.total_ms or processed_ms > total_ms:
                    raise ContractValidationError("progress_out_of_range", "processed_ms")
                if processed_ms <= job.processed_ms:
                    return
                checkpoint = TranscriptionCheckpoint(
                    CHECKPOINT_SCHEMA_VERSION,
                    TranscriptionJobStage.validating_input,
                    processed_ms,
                    None,
                    None,
                    None,
                )
                try:
                    store.update_checkpoint(
                        self.job_id,
                        checkpoint,
                        expected_updated_at=job.updated_at,
                        now=max(int(self.clock()), job.updated_at + 1),
                    )
                    return
                except StoreConflictError:
                    continue
            raise StoreConflictError("progress_cas_conflict")
        finally:
            conn.close()


@dataclass(frozen=True, slots=True)
class StoreCancellationProbe:
    job_id: str
    connect_factory: Callable = connect

    def __post_init__(self) -> None:
        validate_uuid(self.job_id, "job_id")

    def is_cancel_requested(self) -> bool:
        conn = self.connect_factory()
        try:
            return (
                SQLiteTranscriptionStore(conn).load_job(self.job_id).status
                is TranscriptionJobStatus.cancelled
            )
        finally:
            conn.close()


@dataclass(frozen=True, slots=True)
class EventCancellationProbe:
    event: Event

    def is_cancel_requested(self) -> bool:
        return self.event.is_set()


@dataclass(frozen=True, slots=True)
class CompositeCancellationProbe:
    probes: tuple[CancellationProbe, ...]

    def __post_init__(self) -> None:
        if not self.probes:
            raise ContractValidationError("empty_cancellation_probes", "probes")

    def is_cancel_requested(self) -> bool:
        return any(probe.is_cancel_requested() for probe in self.probes)
