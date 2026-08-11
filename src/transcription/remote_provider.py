"""Remote ASR Provider adapter; Canonical construction remains in pipeline.py."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import httpx

from .asr_service_contract import (
    ASR_API_VERSION,
    CreateJobRequest,
    ServiceCapabilities,
    ServiceFailureCode,
    ServiceJob,
    ServiceJobState,
    ServiceResult,
)
from .profile import (
    FasterWhisperRemoteConfig,
    Qwen3AsrRemoteConfig,
    RemoteAsrServiceConfig,
    WhisperXRemoteConfig,
    RemoteProviderConfig,
    TranscriptionExecutionConfig,
)
from .provider_protocol import (
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderCandidate,
    ProviderFailure,
    ProviderFailureClassification,
    ProviderResult,
)
from .provider_registry import ProviderRuntimePorts
from .runtime_ports import InputPart, ProviderRuntimeState
from .types import (
    ContractValidationError,
    TranscriptionInputRef,
    canonical_json_bytes,
    sha256_hex,
    validate_provider_key,
    validate_uuid,
)


class AsrServiceClientError(RuntimeError):
    def __init__(
        self,
        status_code: int | None,
        reason: str,
        detail_code: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.detail_code = detail_code
        super().__init__(reason)


class AsrServiceClient(Protocol):
    def capabilities(self) -> ServiceCapabilities: ...

    def create_job(self, request: CreateJobRequest) -> ServiceJob: ...

    def upload_part(self, job_id: str, part: InputPart) -> ServiceJob: ...

    def complete_input(self, job_id: str) -> ServiceJob: ...

    def start_job(self, job_id: str) -> ServiceJob: ...

    def get_job(self, job_id: str) -> ServiceJob: ...

    def cancel_job(self, job_id: str) -> ServiceJob: ...

    def get_result(self, job_id: str) -> ServiceResult: ...


@dataclass(slots=True)
class HttpxAsrServiceClient:
    """Short-request HTTP client. URL and token are deployment inputs only."""

    base_url: str
    token: str
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 60.0
    transport: httpx.BaseTransport | None = None
    verify_tls: bool = True

    def __post_init__(self) -> None:
        if type(self.base_url) is not str or not self.base_url.startswith(
            ("http://", "https://")
        ):
            raise ContractValidationError("invalid_service_url", "base_url")
        if type(self.token) is not str or not self.token:
            raise ContractValidationError("empty_string", "token")
        if type(self.verify_tls) is not bool:
            raise ContractValidationError("invalid_boolean", "verify_tls")
        if not self.verify_tls:
            parsed_url = httpx.URL(self.base_url)
            if (
                parsed_url.scheme != "http"
                or parsed_url.host != "127.0.0.1"
                or parsed_url.username
                or parsed_url.password
            ):
                raise ContractValidationError(
                    "insecure_transport_forbidden", "verify_tls"
                )
        if (
            type(self.connect_timeout_seconds) not in (int, float)
            or self.connect_timeout_seconds <= 0
            or type(self.request_timeout_seconds) not in (int, float)
            or self.request_timeout_seconds <= 0
        ):
            raise ContractValidationError("invalid_timeout", "http_client")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> object:
        request_headers = {"Authorization": f"Bearer {self.token}"}
        if headers:
            request_headers.update(headers)
        timeout = httpx.Timeout(
            self.request_timeout_seconds,
            connect=self.connect_timeout_seconds,
        )
        try:
            with httpx.Client(
                base_url=self.base_url.rstrip("/"),
                headers=request_headers,
                timeout=timeout,
                transport=self.transport,
                verify=self.verify_tls,
            ) as client:
                response = client.request(
                    method,
                    path,
                    json=json_body,
                    content=content,
                )
        except httpx.TimeoutException as exc:
            raise AsrServiceClientError(None, "timeout") from exc
        except httpx.HTTPError as exc:
            raise AsrServiceClientError(None, "connect_error") from exc
        if response.status_code >= 400:
            detail_code = None
            try:
                payload = response.json()
                detail = payload.get("detail") if type(payload) is dict else None
                code = detail.get("code") if type(detail) is dict else None
                if type(code) is str and 0 < len(code) <= 100 and code.replace("_", "").isalnum():
                    detail_code = code
            except ValueError:
                pass
            raise AsrServiceClientError(
                response.status_code, "http_error", detail_code
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AsrServiceClientError(
                response.status_code, "invalid_json"
            ) from exc

    def capabilities(self) -> ServiceCapabilities:
        return ServiceCapabilities.from_json_dict(
            self._request("GET", "/v1/capabilities")
        )

    def create_job(self, request: CreateJobRequest) -> ServiceJob:
        return ServiceJob.from_json_dict(
            self._request("POST", "/v1/jobs", json_body=request.to_json_dict())
        )

    def upload_part(self, job_id: str, part: InputPart) -> ServiceJob:
        return ServiceJob.from_json_dict(
            self._request(
                "PUT",
                f"/v1/jobs/{job_id}/input/{part.part_number}",
                content=part.content,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Offset-Bytes": str(part.offset_bytes),
                    "X-Content-Sha256": part.content_sha256,
                },
            )
        )

    def complete_input(self, job_id: str) -> ServiceJob:
        return ServiceJob.from_json_dict(
            self._request("POST", f"/v1/jobs/{job_id}/input/complete")
        )

    def start_job(self, job_id: str) -> ServiceJob:
        return ServiceJob.from_json_dict(
            self._request("POST", f"/v1/jobs/{job_id}/start")
        )

    def get_job(self, job_id: str) -> ServiceJob:
        return ServiceJob.from_json_dict(
            self._request("GET", f"/v1/jobs/{job_id}")
        )

    def cancel_job(self, job_id: str) -> ServiceJob:
        return ServiceJob.from_json_dict(
            self._request("POST", f"/v1/jobs/{job_id}/cancel")
        )

    def get_result(self, job_id: str) -> ServiceResult:
        return ServiceResult.from_json_dict(
            self._request("GET", f"/v1/jobs/{job_id}/result")
        )


_FAILURE_MAP = {
    ServiceFailureCode.profile_unavailable: ProviderErrorCode.provider_unavailable,
    ServiceFailureCode.service_unavailable: ProviderErrorCode.provider_unavailable,
    ServiceFailureCode.queue_full: ProviderErrorCode.provider_unavailable,
    ServiceFailureCode.provider_timeout: ProviderErrorCode.provider_timeout,
    ServiceFailureCode.provider_oom: ProviderErrorCode.provider_oom,
    ServiceFailureCode.provider_cancelled: ProviderErrorCode.provider_cancelled,
    ServiceFailureCode.input_too_large: ProviderErrorCode.input_too_large,
    ServiceFailureCode.contract_mismatch: ProviderErrorCode.service_contract_mismatch,
    ServiceFailureCode.input_hash_mismatch: ProviderErrorCode.invalid_input,
    ServiceFailureCode.input_incomplete: ProviderErrorCode.invalid_input,
    ServiceFailureCode.invalid_engine_output: ProviderErrorCode.invalid_provider_output,
    ServiceFailureCode.engine_failure_transient: ProviderErrorCode.transient_provider_error,
    ServiceFailureCode.engine_failure_permanent: ProviderErrorCode.permanent_provider_error,
    ServiceFailureCode.storage_unavailable: ProviderErrorCode.provider_unavailable,
    ServiceFailureCode.disk_low: ProviderErrorCode.provider_unavailable,
}


def _failure(
    provider_key: str,
    code: ProviderErrorCode,
    timeout_ms: int | None = None,
) -> ProviderFailure:
    transient = code in {
        ProviderErrorCode.provider_unavailable,
        ProviderErrorCode.provider_timeout,
        ProviderErrorCode.provider_oom,
        ProviderErrorCode.transient_provider_error,
    }
    return ProviderFailure(
        provider_key,
        code,
        ProviderFailureClassification.transient
        if transient
        else ProviderFailureClassification.permanent,
        timeout_ms if code is ProviderErrorCode.provider_timeout else None,
    )


def _client_failure(
    provider_key: str,
    exc: AsrServiceClientError,
    timeout_ms: int,
) -> ProviderFailure:
    if exc.status_code in (401, 403):
        return _failure(provider_key, ProviderErrorCode.permanent_provider_error)
    if exc.status_code == 409 and exc.detail_code == "identity_conflict":
        return _failure(
            provider_key, ProviderErrorCode.service_request_identity_conflict
        )
    if exc.status_code == 409:
        return _failure(provider_key, ProviderErrorCode.service_contract_mismatch)
    if exc.status_code == 413:
        return _failure(provider_key, ProviderErrorCode.input_too_large)
    if exc.reason == "timeout":
        return _failure(provider_key, ProviderErrorCode.provider_timeout, timeout_ms)
    if exc.reason == "invalid_json":
        return _failure(provider_key, ProviderErrorCode.service_contract_mismatch)
    if exc.status_code == 503 or exc.status_code is None:
        return _failure(provider_key, ProviderErrorCode.provider_unavailable)
    return _failure(provider_key, ProviderErrorCode.provider_contract_violation)


def compute_client_request_id(
    application_job_id: str,
    input_ref: TranscriptionInputRef,
    execution: TranscriptionExecutionConfig,
) -> str:
    validate_uuid(application_job_id, "application_job_id")
    config = execution.provider_config
    if type(config) not in (
        RemoteAsrServiceConfig,
        FasterWhisperRemoteConfig,
        Qwen3AsrRemoteConfig,
        WhisperXRemoteConfig,
    ):
        raise ContractValidationError("invalid_provider_config", "provider_config")
    return sha256_hex(
        canonical_json_bytes(
            {
                "application_job_id": application_job_id,
                "media_id": input_ref.media_id,
                "audio_sha256": input_ref.content_sha256,
                "size": input_ref.size_bytes,
                "duration": input_ref.duration_ms,
                "provider": execution.provider_key,
                "profile": config.service_profile_id,
                "execution_fingerprint": execution.execution_fingerprint,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class RemoteAsrProvider:
    client: AsrServiceClient
    ports: ProviderRuntimePorts
    _provider_key: str
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        validate_provider_key(self._provider_key)

    @property
    def provider_key(self) -> str:
        return self._provider_key

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            self.provider_key, ("zh-CN",), ("audio",), True, False, None
        )

    def _cancelled(self, job_id: str | None) -> ProviderFailure | None:
        if not self.ports.cancellation_probe.is_cancel_requested():
            return None
        if job_id is not None:
            try:
                self.client.cancel_job(job_id)
            except Exception:
                pass
        return _failure(self.provider_key, ProviderErrorCode.provider_cancelled)

    def _interrupted(
        self,
        job_id: str | None,
        deadline: float,
        timeout_ms: int,
    ) -> ProviderFailure | None:
        cancelled = self._cancelled(job_id)
        if cancelled is not None:
            return cancelled
        if self.monotonic() < deadline:
            return None
        if job_id is not None:
            try:
                self.client.cancel_job(job_id)
            except Exception:
                pass
        return _failure(
            self.provider_key,
            ProviderErrorCode.provider_timeout,
            timeout_ms,
        )

    def _upload(
        self,
        job_id: str,
        input_ref: TranscriptionInputRef,
        config: RemoteProviderConfig,
        deadline: float,
        timeout_ms: int,
    ) -> ProviderFailure | None:
        expected_number = 0
        expected_offset = 0
        content_hash = hashlib.sha256()
        try:
            parts = self.ports.input_source.iter_parts(
                input_ref, config.upload_part_bytes
            )
            for part in parts:
                interrupted = self._interrupted(job_id, deadline, timeout_ms)
                if interrupted is not None:
                    return interrupted
                if (
                    type(part) is not InputPart
                    or part.part_number != expected_number
                    or part.offset_bytes != expected_offset
                    or len(part.content) > config.upload_part_bytes
                ):
                    return _failure(
                        self.provider_key, ProviderErrorCode.input_unavailable
                    )
                self.client.upload_part(job_id, part)
                content_hash.update(part.content)
                expected_number += 1
                expected_offset += len(part.content)
        except (ContractValidationError, OSError):
            return _failure(self.provider_key, ProviderErrorCode.input_unavailable)
        if (
            expected_offset != input_ref.size_bytes
            or content_hash.hexdigest() != input_ref.content_sha256
        ):
            return _failure(self.provider_key, ProviderErrorCode.input_unavailable)
        return None

    def transcribe(
        self,
        input_ref: TranscriptionInputRef,
        execution: TranscriptionExecutionConfig,
    ) -> ProviderResult:
        if execution.provider_key != self.provider_key:
            return _failure(
                self.provider_key, ProviderErrorCode.service_contract_mismatch
            )
        config = execution.provider_config
        if type(config) not in (
            RemoteAsrServiceConfig,
            FasterWhisperRemoteConfig,
            Qwen3AsrRemoteConfig,
            WhisperXRemoteConfig,
        ):
            return _failure(
                self.provider_key, ProviderErrorCode.service_contract_mismatch
            )
        if config.config_kind != self.provider_key:
            return _failure(
                self.provider_key, ProviderErrorCode.service_contract_mismatch
            )
        deadline = self.monotonic() + execution.timeout_ms / 1000
        job_id: str | None = None
        try:
            interrupted = self._interrupted(
                None, deadline, execution.timeout_ms
            )
            if interrupted is not None:
                return interrupted
            capabilities = self.client.capabilities()
            if (
                capabilities.api_version != config.expected_api_version
                or config.service_profile_id not in capabilities.service_profiles
                or config.upload_part_bytes > capabilities.max_upload_part_bytes
            ):
                return _failure(
                    self.provider_key, ProviderErrorCode.service_contract_mismatch
                )
            if input_ref.size_bytes > capabilities.max_input_bytes:
                return _failure(self.provider_key, ProviderErrorCode.input_too_large)

            request = CreateJobRequest(
                ASR_API_VERSION,
                compute_client_request_id(
                    self.ports.application_job_id, input_ref, execution
                ),
                self.provider_key,
                config.service_profile_id,
                execution.execution_fingerprint,
                input_ref,
            )
            interrupted = self._interrupted(
                None, deadline, execution.timeout_ms
            )
            if interrupted is not None:
                return interrupted
            job = self.client.create_job(request)
            job_id = job.job_id
            if (
                job.client_request_id != request.client_request_id
                or job.total_ms != input_ref.duration_ms
            ):
                return _failure(
                    self.provider_key, ProviderErrorCode.service_contract_mismatch
                )

            if job.state in (ServiceJobState.created, ServiceJobState.uploading):
                upload_failure = self._upload(
                    job_id,
                    input_ref,
                    config,
                    deadline,
                    execution.timeout_ms,
                )
                if upload_failure is not None:
                    return upload_failure
                job = self.client.complete_input(job_id)
            interrupted = self._interrupted(
                job_id, deadline, execution.timeout_ms
            )
            if interrupted is not None:
                return interrupted
            if job.state is ServiceJobState.paused:
                job = self.client.start_job(job_id)
            elif job.state is ServiceJobState.queued:
                job = self.client.start_job(job_id)

            while job.state not in {
                ServiceJobState.succeeded,
                ServiceJobState.failed,
                ServiceJobState.cancelled,
            }:
                interrupted = self._interrupted(
                    job_id, deadline, execution.timeout_ms
                )
                if interrupted is not None:
                    return interrupted
                self.ports.progress_sink.record(
                    job_id,
                    job.processed_ms,
                    job.total_ms,
                    ProviderRuntimeState(job.state.value),
                    None if job.pause_reason is None else job.pause_reason.value,
                )
                self.sleep(config.poll_interval_ms / 1000)
                job = self.client.get_job(job_id)

            interrupted = self._interrupted(
                job_id, deadline, execution.timeout_ms
            )
            if interrupted is not None:
                return interrupted

            if job.state is ServiceJobState.cancelled:
                return _failure(
                    self.provider_key, ProviderErrorCode.provider_cancelled
                )
            if job.state is ServiceJobState.failed:
                code = _FAILURE_MAP.get(
                    job.failure_code, ProviderErrorCode.permanent_provider_error
                )
                return _failure(
                    self.provider_key,
                    code,
                    execution.timeout_ms
                    if code is ProviderErrorCode.provider_timeout
                    else None,
                )
            try:
                service_result = self.client.get_result(job_id)
            except ContractValidationError:
                return _failure(
                    self.provider_key, ProviderErrorCode.invalid_provider_output
                )
            if type(service_result) is not ServiceResult:
                return _failure(
                    self.provider_key, ProviderErrorCode.invalid_provider_output
                )
            if service_result.job_id != job_id:
                return _failure(
                    self.provider_key, ProviderErrorCode.service_contract_mismatch
                )
            result = service_result.result
            if result.provider_key != self.provider_key:
                return _failure(
                    self.provider_key, ProviderErrorCode.service_contract_mismatch
                )
            try:
                if type(result) is ProviderCandidate:
                    return ProviderCandidate.from_json_dict(result.to_json_dict())
                if type(result) is ProviderFailure:
                    return ProviderFailure.from_json_dict(result.to_json_dict())
            except ContractValidationError:
                return _failure(
                    self.provider_key, ProviderErrorCode.invalid_provider_output
                )
            return _failure(
                self.provider_key, ProviderErrorCode.invalid_provider_output
            )
        except AsrServiceClientError as exc:
            return _client_failure(self.provider_key, exc, execution.timeout_ms)
        except ContractValidationError:
            return _failure(
                self.provider_key, ProviderErrorCode.service_contract_mismatch
            )
        except Exception:
            return _failure(
                self.provider_key, ProviderErrorCode.provider_contract_violation
            )
