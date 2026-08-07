"""config_optimizer — recommend llama-server args for a GPU + model.

Generalizes the manual tuning done for the Qwen3.6-35B MoE on a 16 GB card
(ctx=100k, ub=1024 fits ~14.6 GB). Given the GPU's total VRAM and a model
file (or its size in GB), estimate weights VRAM, KV cache, and the compute
buffer, then recommend --ctx, -ub, -ctk/-ctv, -fa, -ngl, --kv-offload.

Outputs a ready-to-paste llama-server invocation plus CORTEXAGENT_* env exports
(so it's useful to CortexAgent users too) and an SLIMTOKEN_* summary.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def detect_vram_gb() -> Optional[float]:
    """Auto-detect total GPU VRAM in GB via nvidia-smi. None on failure."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout
        nums = re.findall(r"\d+", out)
        if nums:
            return int(nums[0]) / 1024.0
    except Exception:
        return None
    return None


@dataclass
class Recommendation:
    vram_gb: float
    model_path: str
    weights_gb: float
    ctx: int
    ub: int
    ctk: str = "q4_0"
    ctv: str = "q4_0"
    fa: str = "on"
    ngl: int = 999
    kv_offload: int = 1
    np: int = 1
    b: int = 2048
    kv_per_token_bytes: int = 5120   # ~5 KB/token (hybrid MoE q4_0); dense ~8 KB
    est_total_gb: float = 0.0
    margin_gb: float = 0.0
    notes: list = field(default_factory=list)

    def llama_server_cmd(self, port: int = 8080, alias: str = "slimtoken") -> str:
        args = [
            "llama-server", "-m", self.model_path,
            "-c", str(self.ctx), "-ngl", str(self.ngl),
            "-fa", self.fa, "-ctk", self.ctk, "-ctv", self.ctv,
            "-np", str(self.np), "-b", str(self.b), "-ub", str(self.ub),
            "--alias", alias, "--host", "127.0.0.1", "--port", str(port),
            "--kv-unified",
        ]
        if self.kv_offload == 0:
            args.append("--no-kv-offload")
        return " ".join(args)

    def cortexagent_env(self) -> str:
        return "\n".join([
            f"export CORTEXAGENT_CTX={self.ctx}",
            f"export CORTEXAGENT_UB={self.ub}",
            f"export CORTEXAGENT_NGL={self.ngl}",
            f'export CORTEXAGENT_FA={self.fa}',
            f"export CORTEXAGENT_CTK={self.ctk}",
            f"export CORTEXAGENT_CTV={self.ctv}",
            f"export CORTEXAGENT_NP={self.np}",
            f"export CORTEXAGENT_KV_OFFLOAD={self.kv_offload}",
        ])


def _model_size_gb(model_path: Optional[str], model_size_gb: Optional[float]) -> float:
    if model_size_gb is not None:
        return float(model_size_gb)
    if model_path and Path(model_path).exists():
        return Path(model_path).stat().st_size / (1024 ** 3)
    raise ValueError("provide --model PATH or --model-size-gb N")


def recommend(vram_gb: Optional[float] = None,
              model_path: Optional[str] = None,
              model_size_gb: Optional[float] = None,
              kv_per_token_bytes: int = 5120,
              native_ctx: int = 262144,
              keep_free_gb: float = 1.0) -> Recommendation:
    """Compute a Recommendation. Weights VRAM ≈ gguf file size * loading factor (all GPU)."""
    if vram_gb is None:
        vram_gb = detect_vram_gb()
        if vram_gb is None:
            raise ValueError("could not auto-detect VRAM; pass --vram-gb N")
    # A loaded gguf consumes slightly MORE VRAM than its file size (dequant/
    # eval tables, CUDA context). ~1.05 is a conservative factor calibrated
    # against a 12.74 GB IQ3_S file that occupies ~13.2 GB once loaded.
    weights = _model_size_gb(model_path, model_size_gb) * 1.05
    # Budget: total - weights - CUDA context floor - safety keep-free.
    floor = 0.4 + keep_free_gb  # ~0.4 GB per-process CUDA context + headroom
    free_for_kv_and_buf = vram_gb - weights - floor
    notes = []
    if free_for_kv_and_buf <= 0:
        # Model alone doesn't fit; recommend max offload + min everything.
        notes.append("WARNING: weights exceed VRAM — recommend a smaller quant "
                     "or partial GPU offload (-ngl <n).")
        return Recommendation(vram_gb=vram_gb, model_path=model_path or "",
                              weights_gb=round(weights, 2), ctx=8192, ub=512,
                              kv_offload=0, est_total_gb=round(weights, 2),
                              margin_gb=0.0, notes=notes,
                              kv_per_token_bytes=kv_per_token_bytes)

    # KV cache size at a candidate ctx: ctx * kv_per_token_bytes / 1024**3 GB.
    # The --kv-unified compute buffer scales ~linearly with ubatch (NOT ctx):
    #   measured 0.43 GB @ ub=512, 1.72 GB @ ub=2048  ->  buf = ub * 0.00084 GB
    # Pick the largest ctx in [16k, 32k, 64k, 100k, 128k, 200k, 256k] that fits
    # with ub=1024, falling back to ub=512. (ub=256 is too slow to prefer.)
    BUF_GB_PER_UB = 0.00084
    candidates = [16384, 32768, 65536, 100000, 131072, 200000, 262144]
    candidates = [c for c in candidates if c <= native_ctx]
    chosen_ctx, chosen_ub = 16384, 512
    for ctx in sorted(candidates, reverse=True):
        kv_gb = ctx * kv_per_token_bytes / (1024 ** 3)
        for ub in (1024, 512):
            buf_gb = ub * BUF_GB_PER_UB
            if kv_gb + buf_gb <= free_for_kv_and_buf:
                chosen_ctx, chosen_ub = ctx, ub
                notes.append(f"ctx={ctx} ub={ub}: KV={kv_gb:.2f}GB "
                             f"buf={buf_gb:.2f}GB fits in {free_for_kv_and_buf:.2f}GB free")
                # take the largest ctx at the largest ub that fits
                break
        if chosen_ctx == ctx:
            break
    kv_gb = chosen_ctx * kv_per_token_bytes / (1024 ** 3)
    buf_gb = chosen_ub * BUF_GB_PER_UB
    est = weights + kv_gb + buf_gb + floor
    rec = Recommendation(
        vram_gb=vram_gb, model_path=model_path or "",
        weights_gb=round(weights, 2), ctx=chosen_ctx, ub=chosen_ub,
        kv_offload=1, est_total_gb=round(est, 2),
        margin_gb=round(vram_gb - est, 2), notes=notes,
        kv_per_token_bytes=kv_per_token_bytes)
    return rec


def format_report(rec: Recommendation) -> str:
    lines = [
        "slimtoken — recommended llama-server args",
        "=" * 56,
        f"GPU VRAM:        {rec.vram_gb:.1f} GB",
        f"Model:           {rec.model_path or '(size-only)'}",
        f"Weights VRAM:    {rec.weights_gb:.2f} GB",
        f"Est. total:      {rec.est_total_gb:.2f} GB  (margin {rec.margin_gb:+.2f} GB)",
        "-" * 56,
        f"ctx={rec.ctx}  ub={rec.ub}  fa={rec.fa}  ctk/ctv={rec.ctk}/{rec.ctv}  "
        f"ngl={rec.ngl}  np={rec.np}  kv_offload={rec.kv_offload}",
        "-" * 56,
        rec.llama_server_cmd(),
        "-" * 56,
        "# CortexAgent env exports:",
        rec.cortexagent_env(),
    ]
    for n in rec.notes:
        lines.append(f"  · {n}")
    lines.append("")
    lines.append("# Estimate only — verify VRAM with nvidia-smi under a real prompt")
    lines.append("# before trusting the margin. Buffer is calibrated for --kv-unified")
    lines.append("# on a hybrid MoE; dense models or --kv-budged change the math.")
    return "\n".join(lines)