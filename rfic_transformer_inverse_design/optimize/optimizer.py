"""Config-selectable optimizer facade for the transformer flow."""

from __future__ import annotations

from collections.abc import Callable

from ..core.types import TransformerEvalResult
from ..execution.evaluator import TransformerEmxEvaluator
from .backends import _CMAESBackend, _TuRBOBackend

class TransformerOptimizer:
    """Config-selectable optimizer facade for the transformer flow."""

    _BACKENDS = {
        "cma_es": _CMAESBackend,
        "turbo": _TuRBOBackend,
    }

    def __init__(
        self,
        evaluator: TransformerEmxEvaluator,
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
    ):
        self.evaluator = evaluator
        self.progress_callback = progress_callback
        self.should_stop = should_stop
        self.should_pause = should_pause

    def optimize(self) -> TransformerEvalResult:
        optimizer_name = self.evaluator.run_config.optimizer.name
        try:
            backend_cls = self._BACKENDS[optimizer_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported optimizer backend '{optimizer_name}'") from exc
        backend = backend_cls(
            self.evaluator,
            progress_callback=self.progress_callback,
            should_stop=self.should_stop,
            should_pause=self.should_pause,
        )
        return backend.optimize()
