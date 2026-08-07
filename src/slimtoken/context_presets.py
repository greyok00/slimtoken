"""context_presets — high-context VRAM-tier configs (dense AND MoE) that show
how slimtoken's compression expands the effective context window.

For each GPU VRAM tier this provides TWO recommendations:

  * **standard (dense)** — a dense model pushed to the largest nominal context
    that fits fully in VRAM.
  * **MoE** — a Mamba-2 / MoE hybrid (Qwen3.6-35B-A3B, LFM2.5-8B-A1B) whose
    per-token KV is tiny (~5 KB/token at q4_0 vs ~30 KB/token for dense), so it
    reaches far larger contexts on the same VRAM. These are the models the
    author actually runs (see the CortexAgent fit configs).

Every number is **computed at runtime** by :func:`config_optimizer.recommend`
(high-side: ``keep_free_gb=0.3`` + RoPE/YaRN extension to 256k) and
:func:`model_presets.measure_reduction` — none are asserted. The effective
raw-token capacity is ``nominal_ctx / (1 - reduction)``: because slimtoken
compresses input ~84%, a 256k window holds ~1.6M raw conversation tokens.

All configs use **q4_0 KV** (``-ctk q4_0 -ctv q4_0``), flash attention on, full
GPU offload, ``--kv-unified`` — matching a proven local llama-server setup.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import config_optimizer as co
from .model_presets import measure_reduction

# High-side tuning (still fits fully in VRAM):
NATIVE_CTX = 262144   # allow RoPE/YaRN extension to 256k (high side)
KEEP_FREE = 0.3       # tight safety margin — high side, still fully in VRAM
KV_QUANT = "q4_0"     # ctk/ctv — matches the local llama-server setup

# q4_0 KV per token (bytes):
#   dense    ≈ 2 * layers * kv_heads * head_dim * 0.5  (~30 KB/token)
#   MoE/Mamba hybrid ≈ 5 KB/token (Mamba state is near-fixed; tiny attention KV)
KV_DENSE_3B = 28672    # Llama 3.2 3B:  28L, 8 kv heads, 128 head_dim
KV_DENSE_8B = 32768    # Llama 3.1 8B:  32L, 8 kv heads, 128 head_dim
KV_DENSE_14B = 40960   # Qwen 3 14B:   40L, 8 kv heads, 128 head_dim
KV_MOE = 5120          # hybrid Mamba-2 + MoE q4_0 (config_optimizer's calibrated default)

# (vram_gb, kind, model, quant, size_gb, kv_per_token, profile, notes)
_CONTEXT_MODELS = [
    # ── 4 GB ───────────────────────────────────────────────────────────────
    {"vram_gb": 4, "kind": "dense", "model": "Llama 3.2 3B", "quant": "Q4_K_M",
     "size_gb": 2.0, "kv_per_token": KV_DENSE_3B, "profile": "aggressive",
     "notes": "best 4GB dense; aggressive profile"},
    {"vram_gb": 4, "kind": "MoE", "model": "LFM2.5-8B-A1B", "quant": "IQ2_S",
     "size_gb": 2.5, "kv_per_token": KV_MOE, "profile": "aggressive",
     "notes": "8B-A1B MoE at 2-bit; quality loss — dense 3B is usually the better 4GB pick"},

    # ── 8 GB ───────────────────────────────────────────────────────────────
    # 8GB MoE capped at 128k (not the 256k high side): at 256k the Q4_K_M 8B-A1B
    # is a razor fit (~+0.04 GB margin) — any VRAM fluctuation spills it. 128k
    # leaves ~2 GB headroom and still gives ~800k effective with compression.
    {"vram_gb": 8, "kind": "MoE", "model": "LFM2.5-8B-A1B", "quant": "Q4_K_M",
     "size_gb": 4.9, "kv_per_token": KV_MOE, "profile": "balanced",
     "native": 131072,
     "notes": "CortexAgent fallback; Mamba-2+MoE tiny KV. 128k for headroom (256k is a razor fit)"},
    {"vram_gb": 8, "kind": "dense", "model": "Llama 3.1 8B", "quant": "Q4_K_M",
     "size_gb": 4.9, "kv_per_token": KV_DENSE_8B, "profile": "balanced",
     "notes": "8B dense; smaller context than the MoE on 8GB"},
    {"vram_gb": 8, "kind": "dense", "model": "Llama 3.2 3B", "quant": "Q4_K_M",
     "size_gb": 2.0, "kv_per_token": KV_DENSE_3B, "profile": "aggressive",
     "notes": "drop to 3B dense for max dense context (128k)"},

    # ── 16 GB ──────────────────────────────────────────────────────────────
    # 16GB MoE capped at 128k — the proven-stable value on the author's RTX
    # 3080 Ti (256k OOMed at ub=2048; 128k@ub512 measured 13.7GB). native=131072.
    {"vram_gb": 16, "kind": "MoE", "model": "Qwen3.6-35B-A3B", "quant": "IQ3_S",
     "size_gb": 11.4, "kv_per_token": KV_MOE, "profile": "balanced",
     "native": 131072,
     "notes": "CortexAgent PRIMARY (measured 13.7GB @128k/ub512); proven-stable 128k"},
    {"vram_gb": 16, "kind": "dense", "model": "Llama 3.1 8B", "quant": "Q4_K_M",
     "size_gb": 4.9, "kv_per_token": KV_DENSE_8B, "profile": "balanced",
     "notes": "8B dense → 256k; the headline dense config"},
]


def list_context_presets(vram_gb: Optional[int] = None) -> List[Dict]:
    """Compute high-context preset rows (dense + MoE) for each VRAM tier.

    Each row: ``{vram_gb, kind, model, quant, kv_quant, nominal_ctx, ub, total_gb,
    margin_gb, profile, reduction_pct, effective_ctx, llama_cmd, notes}``.
    The ``nominal_ctx`` is the largest that fits in VRAM (computed by
    :func:`recommend`); ``effective_ctx`` is ``nominal_ctx / (1 - reduction)``.
    """
    rows: List[Dict] = []
    for m in _CONTEXT_MODELS:
        if vram_gb is not None and m["vram_gb"] != vram_gb:
            continue
        rec = co.recommend(
            vram_gb=m["vram_gb"], model_size_gb=m["size_gb"],
            kv_per_token_bytes=m["kv_per_token"],
            native_ctx=m.get("native", NATIVE_CTX),
            keep_free_gb=KEEP_FREE)
        # force the q4_0 KV + flash + full offload config we document
        rec.ctk, rec.ctv = KV_QUANT, KV_QUANT
        red = measure_reduction(m["profile"], "bloated")["reduction_pct"]
        eff = int(rec.ctx / (1 - red / 100)) if red < 100 else rec.ctx
        rows.append({
            "vram_gb": m["vram_gb"], "kind": m["kind"], "model": m["model"],
            "quant": m["quant"], "kv_quant": KV_QUANT,
            "nominal_ctx": rec.ctx, "ub": rec.ub,
            "total_gb": rec.est_total_gb, "margin_gb": rec.margin_gb,
            "profile": m["profile"], "reduction_pct": red,
            "effective_ctx": eff,
            "llama_cmd": rec.llama_server_cmd(),
            "notes": m["notes"],
        })
    return rows


def best_context_for_tier(vram_gb: int) -> Optional[Dict]:
    """The single largest-effective-context preset for a tier (MoE if it fits
    well, else the dense). Ties broken by effective_ctx."""
    rows = list_context_presets(vram_gb)
    if not rows:
        return None
    return max(rows, key=lambda r: r["effective_ctx"])