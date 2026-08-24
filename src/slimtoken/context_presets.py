
from __future__ import annotations

from typing import Dict, List, Optional

from . import config_optimizer as co
from .model_presets import measure_reduction


NATIVE_CTX = 262144
KEEP_FREE = 0.3
KV_QUANT = "q4_0"




KV_DENSE_3B = 28672
KV_DENSE_8B = 32768
KV_DENSE_14B = 40960
KV_MOE = 5120


_CONTEXT_MODELS = [

    {"vram_gb": 4, "kind": "dense", "model": "Llama 3.2 3B", "quant": "Q4_K_M",
     "size_gb": 2.0, "kv_per_token": KV_DENSE_3B,
     "notes": "best 4GB dense"},
    {"vram_gb": 4, "kind": "MoE", "model": "LFM2.5-8B-A1B", "quant": "IQ2_S",
     "size_gb": 2.5, "kv_per_token": KV_MOE,
     "notes": "8B-A1B MoE at 2-bit; quality loss — dense 3B is usually the better 4GB pick"},





    {"vram_gb": 8, "kind": "MoE", "model": "LFM2.5-8B-A1B", "quant": "Q4_K_M",
     "size_gb": 4.9, "kv_per_token": KV_MOE,
     "native": 131072,
     "notes": "CortexAgent fallback; Mamba-2+MoE tiny KV. 128k for headroom (256k is a razor fit)"},
    {"vram_gb": 8, "kind": "dense", "model": "Llama 3.1 8B", "quant": "Q4_K_M",
     "size_gb": 4.9, "kv_per_token": KV_DENSE_8B,
     "notes": "8B dense; smaller context than the MoE on 8GB"},
    {"vram_gb": 8, "kind": "dense", "model": "Llama 3.2 3B", "quant": "Q4_K_M",
     "size_gb": 2.0, "kv_per_token": KV_DENSE_3B,
     "notes": "drop to 3B dense for max dense context (128k)"},




    {"vram_gb": 16, "kind": "MoE", "model": "Qwen3.6-35B-A3B", "quant": "IQ3_S",
     "size_gb": 11.4, "kv_per_token": KV_MOE,
     "native": 131072,
     "notes": "CortexAgent PRIMARY (measured 13.7GB @128k/ub512); proven-stable 128k"},
    {"vram_gb": 16, "kind": "dense", "model": "Llama 3.1 8B", "quant": "Q4_K_M",
     "size_gb": 4.9, "kv_per_token": KV_DENSE_8B,
     "notes": "8B dense → 256k; the headline dense config"},
]


def list_context_presets(vram_gb: Optional[int] = None) -> List[Dict]:

    rows: List[Dict] = []

    red = measure_reduction("bloated")["reduction_pct"]
    for m in _CONTEXT_MODELS:
        if vram_gb is not None and m["vram_gb"] != vram_gb:
            continue
        rec = co.recommend(
            vram_gb=m["vram_gb"], model_size_gb=m["size_gb"],
            kv_per_token_bytes=m["kv_per_token"],
            native_ctx=m.get("native", NATIVE_CTX),
            keep_free_gb=KEEP_FREE)

        rec.ctk, rec.ctv = KV_QUANT, KV_QUANT
        eff = int(rec.ctx / (1 - red / 100)) if red < 100 else rec.ctx
        rows.append({
            "vram_gb": m["vram_gb"], "kind": m["kind"], "model": m["model"],
            "quant": m["quant"], "kv_quant": KV_QUANT,
            "nominal_ctx": rec.ctx, "ub": rec.ub,
            "total_gb": rec.est_total_gb, "margin_gb": rec.margin_gb,
            "reduction_pct": red,
            "effective_ctx": eff,
            "llama_cmd": rec.llama_server_cmd(),
            "notes": m["notes"],
        })
    return rows


def best_context_for_tier(vram_gb: int) -> Optional[Dict]:

    rows = list_context_presets(vram_gb)
    if not rows:
        return None
    return max(rows, key=lambda r: r["effective_ctx"])