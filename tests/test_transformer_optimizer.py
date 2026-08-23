import importlib.util
import unittest

from tests.rfic_transformer_inverse_design.shared import *
from rfic_transformer_inverse_design.optimize.backends import _CMAESBackend


_HAS_CMA = importlib.util.find_spec("cma") is not None
_HAS_TURBO_STACK = all(importlib.util.find_spec(name) is not None for name in ("torch", "botorch", "gpytorch"))
_requires_cma = unittest.skipUnless(_HAS_CMA, "requires optional optimizer extra package: cma")
_requires_turbo_stack = unittest.skipUnless(
    _HAS_TURBO_STACK,
    "requires optional optimizer extras: torch, botorch, gpytorch",
)


class TransformerOptimizerTest(TransformerToolboxTestBase):
    def test_optimizer_warm_starts_from_imported_summary_geometry(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=4,
                warm_start_samples=0,
                cma_es=CMAESOptimizerConfig(population_size=4, sigma0=2.0),
            ),
        )
        optimum = self._make_optimum(cfg)

        with tempfile.TemporaryDirectory() as tmpdir:
            seed_dir = Path(tmpdir) / "seed"
            seed_evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=seed_dir, optimum=optimum)
            seed_result = seed_evaluator.evaluate_geometry(optimum)
            seed_path = Path(tmpdir) / "seed_summary.json"
            seed_path.write_text(json.dumps(seed_result.summary_dict(), indent=2), encoding="utf-8")

            cfg = replace(
                cfg,
                optimizer=replace(
                    cfg.optimizer,
                    warm_start_paths=(str(seed_path),),
                ),
            )
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir) / "run", optimum=optimum)
            backend = _CMAESBackend(evaluator=evaluator)

            warm_results = backend.run_warm_start()

            self.assertEqual(len(warm_results), 1)
            self.assertIsNotNone(warm_results[0].objective)
            self.assertAlmostEqual(warm_results[0].objective.total_cost, 0.0, delta=1.0e-12)
            self.assertEqual(
                warm_results[0].geometry.flat_dict(),
                optimum.flat_dict(),
            )

    def test_cma_es_stops_cleanly_when_remaining_budget_is_smaller_than_mu(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=8,
                warm_start_samples=4,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=8, sigma0=2.0),
            ),
        )
        optimum = self._make_optimum(cfg)

        class _FakeCMA:
            class CMAEvolutionStrategy:
                def __init__(self, initial_mean, sigma0, options):
                    self.mean = np.asarray(initial_mean, dtype=float)
                    self.sp = type("_StrategyParams", (), {"popsize": int(options.get("popsize", 8)), "mu": 5})()
                    self.result = type(
                        "_StrategyResult",
                        (),
                        {"xbest": np.asarray(initial_mean, dtype=float), "fbest": float("inf")},
                    )()

                def stop(self):
                    return {}

                def ask(self, batch_size):
                    return [self.mean.copy() for _ in range(batch_size)]

                def tell(self, batch, scores):
                    self.result = type(
                        "_StrategyResult",
                        (),
                        {"xbest": np.asarray(batch[0], dtype=float), "fbest": float(min(scores))},
                    )()

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            backend = _CMAESBackend(evaluator=evaluator)

            with mock.patch("rfic_transformer_inverse_design.optimize.backends._require_module", return_value=_FakeCMA):
                result = backend.optimize()

            self.assertIsNotNone(result.objective)
            summary = json.loads((Path(tmpdir) / "optimization_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["cma_es"]["termination_reason"], "max_evaluations_reached")

    def test_cma_es_summary_falls_back_when_xbest_is_missing(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=8,
                warm_start_samples=1,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=4, sigma0=2.0),
            ),
        )
        optimum = self._make_optimum(cfg)

        class _FakeCMA:
            class CMAEvolutionStrategy:
                def __init__(self, initial_mean, sigma0, options):
                    self.mean = np.asarray(initial_mean, dtype=float)
                    self.sp = type("_StrategyParams", (), {"popsize": int(options.get("popsize", 4)), "mu": 2})()
                    self.result = type(
                        "_StrategyResult",
                        (),
                        {"xbest": None, "fbest": None},
                    )()

                def stop(self):
                    return {"manual": True}

                def ask(self, batch_size):
                    return [self.mean.copy() for _ in range(batch_size)]

                def tell(self, batch, scores):
                    self.mean = np.asarray(batch[0], dtype=float)

        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            backend = _CMAESBackend(evaluator=evaluator)

            with mock.patch("rfic_transformer_inverse_design.optimize.backends._require_module", return_value=_FakeCMA):
                result = backend.optimize()

            self.assertIsNotNone(result.objective)
            summary = json.loads((Path(tmpdir) / "optimization_summary.json").read_text(encoding="utf-8"))
            self.assertIn("best_candidate_vector", summary["cma_es"])
            self.assertIsNotNone(summary["cma_es"]["best_candidate_cost"])

    def test_optimizer_assigns_distinct_penalties_to_distinct_invalid_geometries(self) -> None:
        cfg = default_run_config("2t2t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=8,
                warm_start_samples=2,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=4, sigma0=2.0),
            ),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = TransformerEmxEvaluator(run_config=cfg, root_dir=Path(tmpdir))
            backend = _CMAESBackend(evaluator=evaluator)

            first = evaluator.evaluate_geometry(replace(cfg.bounds.midpoint(), offset_um=0.0), run_emx=False)
            second = evaluator.evaluate_geometry(replace(cfg.bounds.midpoint(), offset_um=16.0), run_emx=False)

            self.assertIsNotNone(first.error)
            self.assertIsNotNone(second.error)
            self.assertNotEqual(backend.cost_from_result(first), backend.cost_from_result(second))

    @_requires_cma
    def test_optimizer_emits_progress_callbacks(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=24,
                warm_start_samples=4,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=6, sigma0=2.0),
            ),
        )
        optimum = self._make_optimum(cfg)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            events: list[dict[str, object]] = []
            optimizer = TransformerOptimizer(evaluator=evaluator, progress_callback=events.append)
            result = optimizer.optimize()

            self.assertGreater(len(events), 0)
            self.assertTrue(any(bool(event["is_best"]) for event in events))
            self.assertEqual(events[-1]["evaluation_count"], len(events))
            self.assertIsNotNone(result.objective)

            summary = json.loads((Path(tmpdir) / "optimization_summary.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["cancelled"])

    @_requires_cma
    def test_optimizer_can_be_cancelled_via_should_stop_callback(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=24,
                warm_start_samples=6,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=6, sigma0=2.0),
            ),
        )
        optimum = self._make_optimum(cfg)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            events: list[dict[str, object]] = []

            def _progress(event: dict[str, object]) -> None:
                events.append(event)

            optimizer = TransformerOptimizer(
                evaluator=evaluator,
                progress_callback=_progress,
                should_stop=lambda: len(events) >= 2,
            )
            result = optimizer.optimize()

            self.assertGreaterEqual(len(events), 2)
            self.assertIsNotNone(result.objective)
            summary = json.loads((Path(tmpdir) / "optimization_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["cancelled"])
            self.assertEqual(summary["cma_es"]["termination_reason"], "cancelled")

    def test_optimizer_skips_backend_when_all_variables_are_fixed(self) -> None:
        cfg = default_run_config("1t1t")
        midpoint = cfg.bounds.midpoint()
        cfg = replace(
            cfg,
            bounds=replace(
                cfg.bounds,
                primary=replace(
                    cfg.bounds.primary,
                    outer_width_um=(midpoint.primary.outer_width_um, midpoint.primary.outer_width_um),
                    outer_height_um=(midpoint.primary.outer_height_um, midpoint.primary.outer_height_um),
                    trace_width_um=(midpoint.primary.trace_width_um, midpoint.primary.trace_width_um),
                    terminal_y_span_um=(midpoint.primary.terminal_y_span_um, midpoint.primary.terminal_y_span_um),
                    feed_extension_um=(midpoint.primary.feed_extension_um, midpoint.primary.feed_extension_um),
                ),
                secondary=replace(
                    cfg.bounds.secondary,
                    outer_width_um=(midpoint.secondary.outer_width_um, midpoint.secondary.outer_width_um),
                    outer_height_um=(midpoint.secondary.outer_height_um, midpoint.secondary.outer_height_um),
                    trace_width_um=(midpoint.secondary.trace_width_um, midpoint.secondary.trace_width_um),
                    terminal_y_span_um=(midpoint.secondary.terminal_y_span_um, midpoint.secondary.terminal_y_span_um),
                    feed_extension_um=(midpoint.secondary.feed_extension_um, midpoint.secondary.feed_extension_um),
                ),
                offset_um=(midpoint.offset_um, midpoint.offset_um),
            ),
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=8,
                warm_start_samples=2,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=4, sigma0=2.0),
            ),
        )
        optimum = cfg.bounds.midpoint()
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            optimizer = TransformerOptimizer(evaluator=evaluator)
            result = optimizer.optimize()

            self.assertEqual(TransformerOptimizationAdapter(cfg.bounds).field_order(), ())
            self.assertIsNotNone(result.objective)
            self.assertAlmostEqual(result.objective.total_cost, 0.0, delta=1.0e-12)

            summary = json.loads((Path(tmpdir) / "optimization_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["cma_es"]["termination_reason"], "no_optimizable_variables")
            self.assertFalse(summary["cancelled"])

    @_requires_cma
    def test_optimizer_uses_batch_evaluator_when_available(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=24,
                warm_start_samples=4,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=6, sigma0=2.0),
            ),
        )
        optimum = self._make_optimum(cfg)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            optimizer = TransformerOptimizer(evaluator=evaluator)
            optimizer.optimize()

            self.assertGreater(evaluator.batch_calls, 0)

    @_requires_cma
    def test_optimizer_smoke_cma_es_with_fake_evaluator(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=64,
                warm_start_samples=4,
                seed=7,
                cma_es=CMAESOptimizerConfig(population_size=6, sigma0=2.0),
            ),
        )
        summary = self._assert_optimizer_smoke(
            cfg,
            "cma_es",
            optimum_delta={"primary_width_um": 5.0},
        )
        self.assertIn("termination_reason", summary["cma_es"])
        self.assertIn("best_candidate_vector", summary["cma_es"])
    @_requires_turbo_stack
    def test_optimizer_smoke_turbo_with_fake_evaluator(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="turbo",
                max_evaluations=30,
                warm_start_samples=8,
                seed=7,
                turbo=TuRBOOptimizerConfig(num_restarts=6, raw_samples=64),
            ),
        )
        optimum = self._make_optimum(cfg, delta={"primary_width_um": 5.0})
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            optimizer = TransformerOptimizer(evaluator=evaluator)
            baseline = evaluator.evaluate_geometry(cfg.bounds.midpoint())
            result = optimizer.optimize()

            self.assertIsNotNone(result.objective)
            self.assertIsNotNone(baseline.objective)
            self.assertLessEqual(result.objective.total_cost, baseline.objective.total_cost)

            summary_path = Path(tmpdir) / "optimization_summary.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["optimizer_name"], "turbo")
            self.assertIn("turbo", summary)
        self.assertIn("trust_region_state", summary["turbo"])

    def test_dependency_error_is_explicit_when_backend_package_is_missing(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=8,
                warm_start_samples=2,
            ),
        )
        optimum = self._make_optimum(cfg)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=Path(tmpdir), optimum=optimum)
            optimizer = TransformerOptimizer(evaluator=evaluator)
            with mock.patch("rfic_transformer_inverse_design.optimize.backends.importlib.import_module", side_effect=ImportError("missing")):
                with self.assertRaisesRegex(RuntimeError, "install package 'cma'"):
                    optimizer.optimize()

    def test_optimizer_resumes_from_checkpoint_in_same_run_dir(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(
            cfg,
            optimizer=TransformerOptimizerConfig(
                name="cma_es",
                max_evaluations=10,
                warm_start_samples=2,
                seed=7,
                resume_from_checkpoint=True,
                checkpoint_interval_evaluations=1,
                cma_es=CMAESOptimizerConfig(population_size=4, sigma0=2.0),
            ),
        )
        optimum = self._make_optimum(cfg)

        class _FakeCMA:
            class CMAEvolutionStrategy:
                def __init__(self, initial_mean, sigma0, options):
                    self.mean = np.asarray(initial_mean, dtype=float)
                    self.sigma = float(sigma0)
                    self.sp = type("_StrategyParams", (), {"popsize": int(options.get("popsize", 4)), "mu": 2})()
                    self.result = type(
                        "_StrategyResult",
                        (),
                        {"xbest": np.asarray(initial_mean, dtype=float), "fbest": float("inf")},
                    )()

                def stop(self):
                    return {}

                def ask(self, batch_size):
                    return [self.mean.copy() for _ in range(batch_size)]

                def tell(self, batch, scores):
                    self.mean = np.asarray(batch[0], dtype=float)
                    self.result = type(
                        "_StrategyResult",
                        (),
                        {"xbest": np.asarray(batch[0], dtype=float), "fbest": float(min(scores))},
                    )()

        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            events: list[dict[str, object]] = []

            evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=root_dir, optimum=optimum)
            optimizer = TransformerOptimizer(
                evaluator=evaluator,
                progress_callback=events.append,
                should_stop=lambda: len(events) >= 6,
            )

            with mock.patch("rfic_transformer_inverse_design.optimize.backends._require_module", return_value=_FakeCMA):
                optimizer.optimize()

            checkpoint_path = root_dir / "optimization_checkpoint.json"
            self.assertTrue(checkpoint_path.exists())
            first_summary = json.loads((root_dir / "optimization_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(first_summary["cancelled"])
            self.assertEqual(first_summary["total_evaluation_count"], 6)

            resumed_evaluator = FakeOptimizerEvaluator(run_config=cfg, root_dir=root_dir, optimum=optimum)
            resumed_optimizer = TransformerOptimizer(evaluator=resumed_evaluator)
            with mock.patch("rfic_transformer_inverse_design.optimize.backends._require_module", return_value=_FakeCMA):
                resumed_optimizer.optimize()

            final_summary = json.loads((root_dir / "optimization_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(final_summary["resumed_from_checkpoint"])
            self.assertEqual(final_summary["total_evaluation_count"], 10)
            self.assertEqual(final_summary["checkpoint_path"], str(checkpoint_path))
