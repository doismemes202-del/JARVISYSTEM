"""Telemetria da máquina: CPU/RAM (psutil) + GPU/VRAM/temperatura (nvidia-ml-py / NVML)."""
from __future__ import annotations

import logging

import psutil

log = logging.getLogger("jarvis.telemetry")


class GPUProbe:
    def __init__(self) -> None:
        self.ok, self.name = False, "N/A"
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
            n = pynvml.nvmlDeviceGetName(self.h)
            self.name = n.decode() if isinstance(n, bytes) else n
            self.ok = True
        except Exception as e:
            log.warning("NVML indisponível (%s) — telemetria de GPU desativada.", e)

    def read(self) -> dict:
        if not self.ok:
            return {}
        nv, h = self.nv, self.h
        try:
            util = nv.nvmlDeviceGetUtilizationRates(h)
            mem = nv.nvmlDeviceGetMemoryInfo(h)
            temp = nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)
            try:
                power = round(nv.nvmlDeviceGetPowerUsage(h) / 1000, 1)
            except Exception:
                power = None
            return {"gpu_name": self.name, "gpu_util": util.gpu,
                    "vram_used_gb": round(mem.used / 1024**3, 2),
                    "vram_total_gb": round(mem.total / 1024**3, 2),
                    "vram_pct": round(100 * mem.used / mem.total, 1),
                    "gpu_temp": temp, "gpu_power_w": power}
        except Exception:
            return {}


_gpu: GPUProbe | None = None


def gpu() -> GPUProbe:
    global _gpu
    if _gpu is None:
        _gpu = GPUProbe()
    return _gpu


def snapshot() -> dict:
    vm = psutil.virtual_memory()
    data = {"cpu": psutil.cpu_percent(interval=None),
            "ram_pct": vm.percent,
            "ram_used_gb": round(vm.used / 1024**3, 1),
            "ram_total_gb": round(vm.total / 1024**3, 1)}
    data.update(gpu().read())
    return data
