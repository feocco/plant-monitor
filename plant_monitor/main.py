from __future__ import annotations

import asyncio
import signal

from plant_monitor.config import load_plants, load_service_config
from plant_monitor.ha import HomeAssistantClient
from plant_monitor.logging_config import setup_logging
from plant_monitor.monitor import PlantMonitor
from plant_monitor.runtime_state import RuntimeState

SHUTDOWN_TIMEOUT_SECONDS = 10


async def run() -> None:
    config = load_service_config()
    setup_logging(config.log_level)
    plants = load_plants(config.config_path)
    state = RuntimeState.load(config.state_path)
    ha = HomeAssistantClient(config)
    monitor = PlantMonitor(config, plants, ha, state)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    monitor_task = asyncio.create_task(monitor.run(), name="plant-monitor")
    stop_task = asyncio.create_task(stop_event.wait(), name="plant-monitor-stop")
    try:
        done, pending = await asyncio.wait(
            {monitor_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            monitor_task.cancel()
            await _wait_for_shutdown(monitor_task)
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    finally:
        stop_task.cancel()
        if not monitor_task.done():
            monitor_task.cancel()
            await _wait_for_shutdown(monitor_task)


async def _wait_for_shutdown(task: asyncio.Task) -> None:
    try:
        await asyncio.wait_for(task, timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        pass
    except TimeoutError:
        print("Timed out waiting for clean shutdown; exiting.", flush=True)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Interrupted; exiting.", flush=True)


if __name__ == "__main__":
    main()
