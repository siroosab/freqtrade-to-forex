"""Scheduled runner for the OANDA live-price dry-run strategy loop."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from freqtrade.forex.execution import ExecutionMode
from freqtrade.forex.strategy_loop import StrategyStepResult


class StrategyLoopStep(Protocol):
    async def step(self, pair: str, timeframe: str, *, candle_count: int) -> StrategyStepResult: ...


@dataclass(frozen=True)
class WorkerConfig:
    pair: str
    timeframe: str = "5m"
    candle_count: int = 200
    interval_seconds: float = 300.0
    runtime_state_path: str = "user_data/oanda/runtime-state.json"


class DryRunWorker:
    """Run strategy steps on a schedule without broker order execution."""

    def __init__(
        self,
        loop: StrategyLoopStep,
        config: WorkerConfig,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_result: Callable[[StrategyStepResult], None] | None = None,
        execution_mode: ExecutionMode = ExecutionMode.DRY_RUN,
    ) -> None:
        if execution_mode is not ExecutionMode.DRY_RUN:
            raise ValueError("DryRunWorker requires the dry_run execution mode")
        if config.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if config.candle_count < 2:
            raise ValueError("candle_count must be at least 2")
        self.loop = loop
        self.config = config
        self.sleep = sleep
        self.on_result = on_result
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def _sleep_until_next_step(self, delay: float) -> None:
        if delay <= 0:
            return
        try:
            sleep_task = asyncio.create_task(self.sleep(delay))
            stop_task = asyncio.create_task(self._stop_event.wait())
            done, _ = await asyncio.wait(
                {sleep_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                sleep_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sleep_task
            else:
                stop_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stop_task
        finally:
            if "sleep_task" in locals() and not sleep_task.done():
                sleep_task.cancel()
            if "stop_task" in locals() and not stop_task.done():
                stop_task.cancel()
            if "sleep_task" in locals() and "stop_task" in locals():
                with suppress(asyncio.CancelledError):
                    await asyncio.gather(sleep_task, stop_task, return_exceptions=True)

    async def run_once(self) -> StrategyStepResult:
        result = await self.loop.step(
            self.config.pair,
            self.config.timeframe,
            candle_count=self.config.candle_count,
        )
        if self.on_result:
            self.on_result(result)
        return result

    async def run(self, *, max_steps: int | None = None) -> int:
        steps = 0
        while not self._stop_event.is_set() and (max_steps is None or steps < max_steps):
            runtime_state = self._runtime_state()
            if runtime_state == "stopped":
                break
            if runtime_state == "paused":
                await self._sleep_until_next_step(1.0)
                continue
            await self.run_once()
            steps += 1
            if max_steps is None or steps < max_steps:
                await self._sleep_until_next_step(self.config.interval_seconds)
        return steps

    def _runtime_state(self) -> str:
        try:
            with open(self.config.runtime_state_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return "running"
        state = payload.get("state", "running")
        return state if state in {"running", "paused", "stopped"} else "running"
