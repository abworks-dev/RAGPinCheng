import hashlib
from pathlib import Path
from asr_service.storage import LocalJobRepository
from src.transcription.asr_service_contract import *
from src.transcription.runtime_ports import InputPart
from src.transcription.types import TranscriptionInputRef
def test_storage_idempotent_upload(tmp_path:Path):
    data=b"x"; ref=TranscriptionInputRef("11111111-1111-4111-8111-111111111111","audio",hashlib.sha256(data).hexdigest(),1,1000)
    req=CreateJobRequest(ASR_API_VERSION,"1"*64,"funasr-sensevoice","funasr-sensevoice-small-v1","2"*64,ref)
    repo=LocalJobRepository(tmp_path,10); job=repo.create(req)
    part=InputPart(0,0,data,hashlib.sha256(data).hexdigest()); repo.upload(job.job_id,part); repo.upload(job.job_id,part)
    repo.complete_upload(job.job_id)
    assert repo.content(job.job_id)==data
