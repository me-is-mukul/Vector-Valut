"""Detect what this machine can actually run, and recommend a model for it.

The first-run wizard uses this to pick a default the user will not regret. Getting it
wrong is worse than it sounds: recommend a model that does not fit in VRAM and Ollama
silently spills layers to the CPU, so the app appears to work but every answer takes forty
seconds and the user concludes the whole thing is slow.

The rule of thumb the table encodes: a Q4-quantized model needs roughly
``params * 0.6 GB`` of VRAM, plus about 1 GB of headroom for the KV cache and the desktop.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelChoice:
    name: str
    size_gb: float
    label: str
    rationale: str


@dataclass(frozen=True)
class Hardware:
    ram_gb: float
    gpu_name: str | None
    vram_gb: float
    cpu_cores: int
    os_name: str

    @property
    def has_gpu(self) -> bool:
        return self.vram_gb >= 3.5


# Ordered best-to-worst. The wizard offers the first that fits, and shows the rest as
# alternatives so the user can trade speed against quality.
CATALOG: list[ModelChoice] = [
    ModelChoice(
        "qwen2.5:14b",
        9.0,
        "Best quality",
        "Noticeably better at following the citation rules and at the bulk-organize plan.",
    ),
    ModelChoice(
        "qwen2.5:7b",
        4.7,
        "Recommended",
        "The sweet spot: strong grounded answers, comfortably GPU-resident on 6-8 GB.",
    ),
    ModelChoice(
        "qwen2.5:3b",
        2.0,
        "Fast",
        "Good enough for organizing and simple questions. Weaker at staying grounded.",
    ),
    ModelChoice(
        "qwen2.5:1.5b",
        1.0,
        "Lightweight",
        "For modest machines. Expect it to miss nuance in longer documents.",
    ),
]

#: Leave room for the KV cache, the display, and everything else on the desktop.
VRAM_HEADROOM_GB = 1.2

#: Without a GPU the model sits in system RAM, and the OS plus this app need their share.
RAM_HEADROOM_GB = 6.0


def detect() -> Hardware:
    return Hardware(
        ram_gb=_total_ram_gb(),
        gpu_name=_gpu_name(),
        vram_gb=_vram_gb(),
        cpu_cores=_cpu_cores(),
        os_name=f"{platform.system()} {platform.release()}",
    )


def recommend(hardware: Hardware) -> ModelChoice:
    """The largest model that actually fits. Never one that will spill to CPU."""
    budget = (
        hardware.vram_gb - VRAM_HEADROOM_GB
        if hardware.has_gpu
        else hardware.ram_gb - RAM_HEADROOM_GB
    )
    for choice in CATALOG:
        if choice.size_gb <= budget:
            return choice
    return CATALOG[-1]  # the smallest we have; better than nothing


def fits(choice: ModelChoice, hardware: Hardware) -> bool:
    budget = (
        hardware.vram_gb - VRAM_HEADROOM_GB
        if hardware.has_gpu
        else hardware.ram_gb - RAM_HEADROOM_GB
    )
    return choice.size_gb <= budget


def summary(hardware: Hardware) -> str:
    if hardware.has_gpu:
        return (
            f"{hardware.gpu_name} · {hardware.vram_gb:.0f} GB VRAM · {hardware.ram_gb:.0f} GB RAM"
        )
    return f"CPU only ({hardware.cpu_cores} cores) · {hardware.ram_gb:.0f} GB RAM"


# --- probes -----------------------------------------------------------------


def _total_ram_gb() -> float:
    try:
        import psutil

        return float(psutil.virtual_memory().total) / (1024**3)
    except Exception:
        logger.debug("psutil unavailable; assuming 8 GB RAM")
        return 8.0


def _cpu_cores() -> int:
    try:
        import psutil

        return int(psutil.cpu_count(logical=False) or psutil.cpu_count() or 4)
    except Exception:
        return 4


def _nvidia_smi() -> tuple[str, float] | None:
    """nvidia-smi is the only reliable source for real, usable VRAM."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("nvidia-smi failed: %s", exc)
        return None

    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                return parts[0], float(parts[1]) / 1024.0  # MiB -> GiB
            except ValueError:
                continue
    return None


def _gpu_name() -> str | None:
    found = _nvidia_smi()
    return found[0] if found else None


def _vram_gb() -> float:
    found = _nvidia_smi()
    return found[1] if found else 0.0
