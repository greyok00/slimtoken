
from __future__ import annotations

__version__ = "0.5.0"  # memory subpackage



from . import reliability
from . import response_model
from . import loop_guard
from . import pre_flight
from . import post_verify
from . import distiller
from . import coding_practices
from . import extractors
from . import config

from .engine import (

    MEMORY_DIR, HOT_DIR, WARM_DIR, COLD_DIR, ENTERPRISE_DB,

    atomic_append, atomic_append_bytes,

    append, read_last, search,

    cold_list, cold_get, write_cold,

    hot_to_warm_sync,
    PIPE_BUF,
)


retry = reliability.retry
CircuitBreaker = reliability.CircuitBreaker
CircuitBreakerOpenError = reliability.CircuitBreakerOpenError
get_circuit_breaker = reliability.get_circuit_breaker
parse_response = response_model.parse_response
collapse = response_model.collapse
format_visual = response_model.format_visual
render_plain = response_model.render_plain
summarize = response_model.summarize
stream_turn = response_model.stream_turn
ResponseBlock = response_model.ResponseBlock
TextBlock = response_model.TextBlock
ArtifactBlock = response_model.ArtifactBlock
DisclosureBlock = response_model.DisclosureBlock
ToolBlock = response_model.ToolBlock
LoopGuard = loop_guard.LoopGuard
PreFlightGate = pre_flight.PreFlightGate
PreFlightResult = pre_flight.PreFlightResult
verify_before_llm = pre_flight.verify_before_llm
PostResponseVerifier = post_verify.PostResponseVerifier
PostVerifyResult = post_verify.PostVerifyResult
ColdDistiller = distiller.ColdDistiller




TASK_ORDER = config.TASK_ORDER
HOT_LIMIT_MB = config.HOT_LIMIT_MB


WARM_LIMIT_MB = config.WARM_LIMIT_MB
ALLOW_CAP = config.ALLOW_CAP
HOOK_WRITE_TIERS = config.HOOK_WRITE_TIERS
DISTILLER_INTERVAL_SECONDS = config.DISTILLER_INTERVAL_SECONDS

__all__ = [
    "__version__",

    "MEMORY_DIR", "HOT_DIR", "WARM_DIR", "COLD_DIR", "ENTERPRISE_DB",

    "atomic_append", "atomic_append_bytes",

    "append", "read_last", "search",

    "cold_list", "cold_get", "write_cold",

    "hot_to_warm_sync",
    "PIPE_BUF",

    "retry", "CircuitBreaker", "CircuitBreakerOpenError",
    "get_circuit_breaker", "reliability",

    "parse_response", "collapse", "format_visual", "render_plain",
    "summarize", "stream_turn", "ResponseBlock", "TextBlock",
    "ArtifactBlock", "DisclosureBlock", "ToolBlock",

    "LoopGuard", "PreFlightGate", "PreFlightResult", "verify_before_llm",
    "PostResponseVerifier", "PostVerifyResult",
    "ColdDistiller", "extractors",

    "reliability", "response_model", "loop_guard", "pre_flight",
    "post_verify", "distiller", "coding_practices", "extractors", "config",

    "TASK_ORDER", "HOT_LIMIT_MB", "WARM_LIMIT_MB", "ALLOW_CAP",
    "HOOK_WRITE_TIERS", "DISTILLER_INTERVAL_SECONDS",
]
