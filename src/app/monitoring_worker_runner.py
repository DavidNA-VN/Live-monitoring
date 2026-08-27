from __future__ import annotations

from collections.abc import Callable
import logging
import signal
from threading import Event, Lock, Thread
import time
from typing import Any

from app.monitoring_worker import MonitoringWorkerApplication
from core.stream_session import StreamSessionStatus
from core.worker_shutdown import ShutdownReport
from models.worker_heartbeat import WorkerState

logger = logging.getLogger(__name__)


class MonitoringWorkerRunner:
    """Coordinates lifecycle threads, failure propagation, and orderly shutdown."""

    def __init__(
        self,
        application: MonitoringWorkerApplication,
        *,
        command_worker: bool = False,
        stream_id: str | None = None,
        shutdown_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be > 0")
        self.application = application
        self.command_worker = command_worker
        self.stream_id = stream_id
        self.shutdown_timeout = shutdown_timeout
        self.clock = clock

        self._shutdown_event = Event()
        self._command_stop_event = Event()
        self._projection_stop_event = Event()
        self._heartbeat_stop_event = Event()

        self._threads: dict[str, Thread] = {}
        self._failures: list[tuple[str, str]] = []
        self._lock = Lock()
        self._shutdown_lock = Lock()
        self._shutdown_report: ShutdownReport | None = None

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def run(self, *, install_signal_handlers: bool = True) -> int:
        try:
            self.application.ping()

            if install_signal_handlers:
                self._register_signals()

            if not self._shutdown_event.is_set():
                self._start_thread(
                    "projection",
                    self.application.run_projection,
                    self._projection_stop_event,
                )

            if self.command_worker and not self._shutdown_event.is_set():
                self._start_thread(
                    "command_consumer",
                    self.application.run_commands,
                    self._command_stop_event,
                )

            if not self._shutdown_event.is_set():
                self._start_thread(
                    "heartbeat",
                    self.application.run_heartbeat,
                    self._heartbeat_stop_event,
                )

            while not self._shutdown_event.is_set():
                with self._lock:
                    if self._failures:
                        break

                if not self.command_worker and self.stream_id is not None:
                    snapshot = self.application.supervisor.snapshot(self.stream_id)
                    if snapshot is None or snapshot.status in (
                        StreamSessionStatus.STOPPED,
                        StreamSessionStatus.FAILED,
                    ):
                        break

                self._shutdown_event.wait(timeout=0.5)
        finally:
            report = self.shutdown()

        is_clean = (
            report.commands_drained
            and report.streams_drained
            and report.heartbeat_stopped
            and report.projection_stopped
            and report.redis_closed
            and not report.timed_out
            and len(report.errors) == 0
        )
        return 0 if is_clean else 1

    def shutdown(self) -> ShutdownReport:
        with self._shutdown_lock:
            return self._shutdown_locked()

    def _shutdown_locked(self) -> ShutdownReport:
        if self._shutdown_report is not None and self._shutdown_report.redis_closed:
            return self._shutdown_report

        start_time = self.clock()
        deadline = start_time + self.shutdown_timeout
        errors: list[str] = [f"{svc}: {err}" for svc, err in self._failures]

        self._shutdown_event.set()

        # Step 1: Latch and publish STOPPING before draining other services.
        try:
            request_stopping = getattr(
                self.application.heartbeat_service,
                "request_stopping",
                None,
            )
            if request_stopping is not None:
                request_stopping()
            published = self.application.heartbeat_service.publish_heartbeat(
                WorkerState.STOPPING
            )
            if published is False:
                errors.append("heartbeat_publish_stopping: publish failed")
        except Exception as exc:
            logger.warning(
                "Failed to publish STOPPING heartbeat during shutdown: %s", exc
            )
            errors.append(f"heartbeat_publish_stopping: {exc}")

        # Step 2: Stop command consumer and drain in-flight command
        self._command_stop_event.set()
        cmd_thread = self._threads.get("command_consumer")
        if cmd_thread is not None:
            cmd_timeout = max(0.0, deadline - self.clock())
            cmd_thread.join(timeout=cmd_timeout)
            commands_drained = not cmd_thread.is_alive()
            if not commands_drained:
                errors.append("command_consumer: thread join timed out")
        else:
            commands_drained = True

        # Step 3: Stop stream sessions with deadline
        stream_timeout = max(0.0, deadline - self.clock())
        streams_drained = self.application.stop_streams(timeout=stream_timeout)
        if not streams_drained:
            errors.append("supervisor: stream sessions failed to drain")

        # Step 4: Trigger final projection & stop projection thread
        try:
            self.application.projection_service.wake()
        except Exception:
            pass
        self._projection_stop_event.set()
        proj_thread = self._threads.get("projection")
        if proj_thread is not None:
            proj_timeout = max(0.0, deadline - self.clock())
            proj_thread.join(timeout=proj_timeout)
            projection_stopped = not proj_thread.is_alive()
            if not projection_stopped:
                errors.append("projection: thread join timed out")
        else:
            projection_stopped = True

        # Step 5: Stop heartbeat thread
        self._heartbeat_stop_event.set()
        hb_thread = self._threads.get("heartbeat")
        if hb_thread is not None:
            hb_timeout = max(0.0, deadline - self.clock())
            hb_thread.join(timeout=hb_timeout)
            heartbeat_stopped = not hb_thread.is_alive()
            if not heartbeat_stopped:
                errors.append("heartbeat: thread join timed out")
        else:
            heartbeat_stopped = True

        # Step 6: Never close shared Redis while any user can still be alive.
        can_close_redis = (
            commands_drained
            and streams_drained
            and projection_stopped
            and heartbeat_stopped
        )
        if can_close_redis:
            try:
                self.application.close_redis()
                redis_closed = True
            except Exception as exc:
                logger.exception("Failed to close Redis client during shutdown")
                errors.append(f"redis_close: {exc}")
                redis_closed = False
        else:
            redis_closed = False
            errors.append("redis_close: skipped because shutdown users remain active")

        timed_out = self.clock() > deadline

        with self._lock:
            recorded_failures = tuple(self._failures)
        for service, error in recorded_failures:
            rendered = f"{service}: {error}"
            if rendered not in errors:
                errors.append(rendered)

        report = ShutdownReport(
            commands_drained=commands_drained,
            heartbeat_stopped=heartbeat_stopped,
            projection_stopped=projection_stopped,
            streams_drained=streams_drained,
            redis_closed=redis_closed,
            timed_out=timed_out,
            errors=tuple(errors),
        )
        self._shutdown_report = report
        return report

    def _start_thread(
        self,
        name: str,
        target: Callable[[Event], None],
        stop_event: Event,
    ) -> Thread:
        thread = Thread(
            target=self._run_service,
            args=(name, target, stop_event),
            name=f"{self.application.worker_id}-{name}",
            daemon=False,
        )
        self._threads[name] = thread
        thread.start()
        return thread

    def _run_service(
        self,
        name: str,
        target: Callable[[Event], None],
        stop_event: Event,
    ) -> None:
        try:
            target(stop_event)
        except Exception as exc:
            with self._lock:
                self._failures.append((name, str(exc)))
            if not self._shutdown_event.is_set():
                logger.critical(
                    "Background service '%s' crashed in worker '%s'",
                    name,
                    self.application.worker_id,
                    exc_info=True,
                )
                self.request_shutdown()

    def _register_signals(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:
            logger.info("Received shutdown signal (%s)", signum)
            self.request_shutdown()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, AttributeError):
                # Signals might not be supported in certain thread contexts
                pass
