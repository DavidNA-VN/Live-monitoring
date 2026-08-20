from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from threading import BoundedSemaphore
from typing import Callable


class BoundedExecutor:

    def __init__(
        self,
        max_workers: int,
        max_pending_tasks: int,
    ):
        if max_workers <= 0:
            raise ValueError(
                "max_workers must be > 0"
            )

        if max_pending_tasks < 0:
            raise ValueError(
                "max_pending_tasks must be >= 0"
            )

        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=(
                "media-monitor-worker"
            ),
        )

        self.capacity = BoundedSemaphore(
            value=(
                max_workers
                + max_pending_tasks
            )
        )

    def try_submit(
        self,
        function: Callable,
        *args,
        **kwargs,
    ) -> Future | None:

        acquired = self.capacity.acquire(
            blocking=False
        )

        if not acquired:
            return None

        try:
            future = self.executor.submit(
                function,
                *args,
                **kwargs,
            )
        except Exception:
            self.capacity.release()
            raise

        future.add_done_callback(
            self._release_capacity
        )

        return future

    def _release_capacity(
        self,
        _future: Future,
    ) -> None:

        self.capacity.release()

    def shutdown(
        self,
        wait: bool = True,
    ) -> None:

        self.executor.shutdown(
            wait=wait,
            cancel_futures=False,
        )