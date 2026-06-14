"""Platform collectors (monitors).

Each monitor is the thin collector for one platform -- it yields RawPost and
nothing more; the shared pipeline downstream does the expensive work. The base
contract and the telemetry-wrapped runner live in :mod:`app.monitors.base`.
"""

from app.monitors.base import Monitor, MonitorBlocked, MonitorError, run_monitor

__all__ = ["Monitor", "MonitorBlocked", "MonitorError", "run_monitor"]