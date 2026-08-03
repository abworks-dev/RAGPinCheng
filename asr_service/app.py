"""FastAPI application factory for the disabled-by-default ASR service."""
from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import Depends, FastAPI, HTTPException, Request

from src.transcription.asr_service_contract import (
    ASR_API_VERSION,
    CreateJobRequest,
    ServiceCapabilities,
)
from src.transcription.runtime_ports import InputPart
from src.transcription.types import ContractValidationError

from .auth import require_bearer
from .config import AsrServiceSettings
from .engine_protocol import SENSEVOICE_SERVICE_CONFIG
from .engine_registry import EngineRegistration, EngineRegistry
from .engines.funasr_sensevoice import FunAsrSenseVoiceEngine
from .scheduler import FixedBgePriorityProbe, Scheduler
from .storage import LocalJobRepository


_HTTP_STATUS = {
    "identity_conflict": 409,
    "part_conflict": 409,
    "checkpoint_conflict": 409,
    "result_conflict": 409,
    "upload_already_complete": 409,
    "invalid_service_transition": 409,
    "input_too_large": 413,
    "part_too_large": 413,
    "queue_full": 503,
    "storage_corrupt": 503,
    "storage_not_found": 404,
    "service_unavailable": 503,
    "profile_unavailable": 503,
}


def _http_error(exc: ContractValidationError) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_STATUS.get(exc.code, 422),
        detail={"code": exc.code},
    )


def create_app(
    settings: AsrServiceSettings | None = None,
    scheduler: Scheduler | None = None,
) -> FastAPI:
    settings = settings or AsrServiceSettings.from_env()
    settings.validate_for_startup()
    repo = (
        scheduler.repo
        if scheduler is not None
        else LocalJobRepository(
            settings.spool_root,
            settings.max_input_bytes,
            settings.max_upload_part_bytes,
        )
    )
    scheduler = scheduler or Scheduler(
        repo,
        EngineRegistry(
            (
                EngineRegistration(
                    FunAsrSenseVoiceEngine(), SENSEVOICE_SERVICE_CONFIG
                ),
            )
        ),
        FixedBgePriorityProbe(),
        queue_limit=settings.max_queue_length,
        failure_limit=settings.consecutive_failure_limit,
        enabled=settings.enabled,
    )
    stop_event = Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runner = Thread(
            target=scheduler.run_until_stopped,
            args=(stop_event,),
            name="asr-service-scheduler",
            daemon=True,
        )
        runner.start()
        try:
            yield
        finally:
            stop_event.set()
            runner.join()

    app = FastAPI(lifespan=lifespan)
    auth = Depends(require_bearer(settings.token))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok" if settings.enabled else "disabled",
            "api_version": ASR_API_VERSION,
        }

    @app.get("/v1/capabilities", dependencies=[auth])
    def capabilities() -> dict[str, object]:
        profiles = scheduler.engines.available_profile_ids()
        return ServiceCapabilities(
            ASR_API_VERSION,
            profiles if settings.enabled else (),
            settings.max_upload_part_bytes,
            settings.max_input_bytes,
        ).to_json_dict()

    @app.post("/v1/jobs", dependencies=[auth])
    async def create(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
            create_request = CreateJobRequest.from_json_dict(payload)
            existing = repo.find(create_request)
            if existing is not None:
                return existing.to_json_dict()
            scheduler.ensure_accepting_new_jobs()
            if (
                create_request.service_profile_id
                not in scheduler.engines.available_profile_ids()
            ):
                raise ContractValidationError(
                    "profile_unavailable", "service_profile_id"
                )
            return repo.create(create_request).to_json_dict()
        except ContractValidationError as exc:
            raise _http_error(exc) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail={"code": "invalid_request"}
            ) from exc

    @app.put("/v1/jobs/{job_id}/input/{part_number}", dependencies=[auth])
    async def upload(
        job_id: str, part_number: int, request: Request
    ) -> dict[str, object]:
        try:
            content_type = request.headers.get("content-type", "").split(";", 1)[0]
            if content_type.lower() != "application/octet-stream":
                raise ContractValidationError("invalid_request", "content_type")
            content = await request.body()
            part = InputPart(
                part_number,
                int(request.headers["x-offset-bytes"]),
                content,
                request.headers["x-content-sha256"],
            )
            return repo.upload(job_id, part).to_json_dict()
        except (ContractValidationError, KeyError, ValueError) as exc:
            error = (
                exc
                if isinstance(exc, ContractValidationError)
                else ContractValidationError("invalid_request", "headers")
            )
            raise _http_error(error) from exc

    @app.post("/v1/jobs/{job_id}/input/complete", dependencies=[auth])
    def complete(job_id: str) -> dict[str, object]:
        try:
            return repo.complete_upload(job_id).to_json_dict()
        except ContractValidationError as exc:
            raise _http_error(exc) from exc

    @app.post("/v1/jobs/{job_id}/start", dependencies=[auth])
    def start(job_id: str) -> dict[str, object]:
        try:
            return scheduler.enqueue(job_id).to_json_dict()
        except ContractValidationError as exc:
            raise _http_error(exc) from exc

    @app.get("/v1/jobs/{job_id}", dependencies=[auth])
    def get(job_id: str) -> dict[str, object]:
        try:
            return repo.get(job_id).to_json_dict()
        except ContractValidationError as exc:
            raise _http_error(exc) from exc

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=[auth])
    def cancel(job_id: str) -> dict[str, object]:
        try:
            return scheduler.cancel(job_id).to_json_dict()
        except ContractValidationError as exc:
            raise _http_error(exc) from exc

    @app.get("/v1/jobs/{job_id}/result", dependencies=[auth])
    def result(job_id: str) -> dict[str, object]:
        try:
            return repo.result(job_id).to_json_dict()
        except ContractValidationError as exc:
            raise _http_error(exc) from exc

    app.state.asr_scheduler = scheduler
    return app
