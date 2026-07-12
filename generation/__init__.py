"""Pure generation helpers shared by ComfyUI node facades."""

from .contracts import (
    ErrorCategory,
    GenerationErrorInfo,
    GenerationReleaseInfo,
    GenerationRequestSpec,
    GenerationResult,
    GenerationState,
    GenerationTiming,
    GenerationUsage,
    PartialOutputPolicy,
    ReleasePolicy,
    SamplingMode,
    SamplingSettings,
    ThinkingMode,
)
from .execution import (
    RUNNING_MODEL,
    CanonicalGenerationError,
    CanonicalGenerationExecutor,
    build_canonical_payload,
)
from .images import image_tensor_to_data_url, image_tensor_to_data_urls
from .payloads import build_chat_payload, normalize_structured_output
from .profiles import (
    FREEFORM_PROFILE,
    FREEFORM_SNAPSHOT_JSON,
    ProfileValidationError,
    TaskProfileSnapshot,
    apply_task_profile,
    load_profiles,
    parse_profile_snapshot,
    parse_profiles_document,
)
from .templates import apply_template, get_template_names, load_templates
from .types import GenerationOptions, StructuredConstraint, parse_text_list

__all__ = [
    "ErrorCategory",
    "FREEFORM_PROFILE",
    "FREEFORM_SNAPSHOT_JSON",
    "GenerationErrorInfo",
    "GenerationOptions",
    "GenerationReleaseInfo",
    "GenerationRequestSpec",
    "GenerationResult",
    "GenerationState",
    "GenerationTiming",
    "GenerationUsage",
    "CanonicalGenerationError",
    "CanonicalGenerationExecutor",
    "PartialOutputPolicy",
    "ProfileValidationError",
    "ReleasePolicy",
    "RUNNING_MODEL",
    "SamplingMode",
    "SamplingSettings",
    "StructuredConstraint",
    "TaskProfileSnapshot",
    "ThinkingMode",
    "apply_task_profile",
    "apply_template",
    "build_chat_payload",
    "build_canonical_payload",
    "get_template_names",
    "image_tensor_to_data_url",
    "image_tensor_to_data_urls",
    "load_profiles",
    "load_templates",
    "normalize_structured_output",
    "parse_profile_snapshot",
    "parse_profiles_document",
    "parse_text_list",
]
