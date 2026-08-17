"""slimtoken — a standalone LLM-request optimization layer.

A drop-in HTTP(S) proxy that minifies Anthropic-style requests (tools, system
prompt, messages — code-fence aware) before forwarding to a local llama-server
OR a cloud HTTPS endpoint. Plus a ``config-optimizer`` that recommends
llama-server args for a given GPU + model.

Pure stdlib at runtime. Install = point ANTHROPIC_BASE_URL at the proxy;
uninstall = restore the prior value. Claude Code's own config is never touched.

Public API:
    from slimtoken.pipeline import minify_request, MinifyConfig
    from slimtoken import prompt_reframe
"""
from .pipeline import (  # noqa: F401
    MinifyConfig,
    MinifyStats,
    minify_request,
)

__version__ = "0.3.6"
__all__ = ["MinifyConfig", "MinifyStats", "minify_request", "prompt_reframe"]