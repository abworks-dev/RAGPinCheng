"""Engine-neutral transcription contracts."""
from .candidate import CandidateSegment
from .canonical import CanonicalSegment, CanonicalTranscript
from .formatter import FormatterContext, format_transcript
from .normalizer import normalize_candidate
from .pipeline import execute_transcription
from .asr_service_contract import ServiceCapabilities, ServiceJob, ServiceResult
from .profile_catalog import build_phase3_profile_catalog
from .provider_registry import ProviderRegistry, ProviderRuntimePorts
from .remote_provider import HttpxAsrServiceClient, RemoteAsrProvider
from .runtime_ports import MemoryInputSource, NeverCancel, NoOpProgressSink
from .persistence import *
from .profile import *
from .provider_protocol import *
from .types import *
from .workflow import *

__all__ = [
    "CandidateSegment", "CanonicalSegment", "CanonicalTranscript", "FormatterContext", "format_transcript",
    "normalize_candidate", "execute_transcription",
    "ServiceCapabilities", "ServiceJob", "ServiceResult",
    "build_phase3_profile_catalog", "ProviderRegistry", "ProviderRuntimePorts",
    "HttpxAsrServiceClient", "RemoteAsrProvider", "MemoryInputSource",
    "NeverCancel", "NoOpProgressSink",
]
