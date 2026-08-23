from tests.rfic_transformer_inverse_design.shared import *

from rfic_transformer_inverse_design.dataset import (
    GROUND_CLEARANCE_AUDIT_FILENAME,
    loaded_input_impedance,
    result_to_dataset_row,
    run_sample_dataset,
    sample_geometries,
    uniformity_report,
)


class TransformerDatasetTest(TransformerToolboxTestBase):
    def test_loaded_input_impedance_uses_two_port_formula(self) -> None:
        z = np.array(
            [
                [
                    [10.0 + 2.0j, 3.0 + 1.0j],
                    [2.0 - 1.0j, 40.0 + 4.0j],
                ]
            ],
            dtype=np.complex128,
        )

        zin = loaded_input_impedance(z, z_load_ohm=50.0)

        expected = z[:, 0, 0] - (z[:, 0, 1] * z[:, 1, 0]) / (z[:, 1, 1] + 50.0)
        np.testing.assert_allclose(zin, expected)

    def test_lhs_sample_geometries_stays_within_active_bounds(self) -> None:
        cfg = default_run_config("1t1t")
        samples = sample_geometries(cfg, count=8, sampler="lhs", seed=99)
        self.assertEqual(len(samples.geometries), 8)
        self.assertEqual(samples.unit_vectors.shape, (8, len(samples.field_order)))
        for geometry in samples.geometries:
            self.assertEqual(cfg.bounds.validate(geometry), [])

    def test_optimized_lhs_preserves_uniform_bins_and_improves_discrepancy(self) -> None:
        cfg = default_run_config("1t1t")
        plain = sample_geometries(cfg, count=64, sampler="lhs", seed=99)
        optimized = sample_geometries(cfg, count=64, sampler="lhs_optimized", seed=99)

        plain_report = uniformity_report(plain.unit_vectors, plain.field_order, bins=8)
        optimized_report = uniformity_report(optimized.unit_vectors, optimized.field_order, bins=8)

        for field in optimized.field_order:
            self.assertEqual(optimized_report["fields"][field]["histogram"], [8] * 8)
        self.assertLess(
            optimized_report["space_filling"]["centered_l2_discrepancy"],
            plain_report["space_filling"]["centered_l2_discrepancy"],
        )

    def test_result_to_dataset_row_adds_loaded_zin_columns(self) -> None:
        cfg = default_run_config("1t1t")
        freqs = np.linspace(*cfg.target.band_edges_hz(), cfg.target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, cfg.target, q_primary=18.0, q_secondary=16.0)
        z_diff = diff.to_z_parameters(z0=cfg.target.differential_reference_impedance_ohm)
        result = TransformerEvalResult(
            cache_key="sample",
            geometry=cfg.bounds.midpoint(),
            target=cfg.target,
            layout=None,
            metrics=None,
            objective=None,
            single_ended_sparams=None,
            differential_sparams=diff,
            differential_z=z_diff,
            work_dir=Path("/tmp/sample"),
            touchstone_path=None,
            command=None,
            error=None,
        )

        row = result_to_dataset_row(result, z_load_ohm=50.0)

        center_idx = int(np.argmin(np.abs(freqs - cfg.target.f0_hz)))
        expected_zin = loaded_input_impedance(z_diff, z_load_ohm=50.0)[center_idx]
        self.assertAlmostEqual(row["zin_center_real_ohm"], expected_zin.real)
        self.assertAlmostEqual(row["zin_center_imag_ohm"], expected_zin.imag)
        self.assertAlmostEqual(row["zin_center_abs_ohm"], abs(expected_zin))
        self.assertIn("zin_abs_min_ohm", row)
        self.assertIn("zin_abs_max_ohm", row)
        self.assertIn("zin_center_return_loss_db_50ohm", row)
        self.assertIn("sparam_reciprocity_error_abs_max", row)
        self.assertIn("sparam_passivity_sigma_max", row)
        self.assertEqual(row["sparam_freq_points"], len(freqs))
        self.assertAlmostEqual(row["sparam_freq_start_hz"], freqs[0])
        self.assertAlmostEqual(row["sparam_freq_stop_hz"], freqs[-1])
        self.assertIn("sdd21_center_db", row)
        self.assertGreaterEqual(row["sparam_passivity_sigma_max"], 0.0)
        self.assertAlmostEqual(row["lp_nh_center"], cfg.target.lp_h * 1.0e9, delta=1e-6)
        self.assertAlmostEqual(row["ls_nh_center"], cfg.target.ls_h * 1.0e9, delta=1e-6)
        self.assertAlmostEqual(row["k_center"], cfg.target.k_target, delta=1e-6)
        self.assertAlmostEqual(row["qp_center"], 18.0, delta=1e-6)
        self.assertAlmostEqual(row["qs_center"], 16.0, delta=1e-6)
        self.assertIn("lp_nh_min", row)
        self.assertIn("qs_max", row)

    def test_run_sample_dataset_create_only_writes_manifest_and_csv(self) -> None:
        cfg = default_run_config("1t1t")
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = run_sample_dataset(
                run_config=cfg,
                out_dir=Path(tmpdir),
                count=3,
                batch_size=2,
                sampler="lhs",
                seed=7,
                run_emx=False,
            )

            self.assertEqual(manifest["requested_count"], 3)
            self.assertTrue(Path(manifest["csv_path"]).exists())
            self.assertTrue((Path(tmpdir) / "dataset_manifest.json").exists())
            self.assertIn("uniformity", manifest)
            self.assertIn("space_filling", manifest["uniformity"])
            self.assertIn("centered_l2_discrepancy", manifest["uniformity"]["space_filling"])
            self.assertIn("nearest_neighbor_distance", manifest["uniformity"]["space_filling"])
            self.assertIn("pairwise_abs_correlation", manifest["uniformity"]["space_filling"])
            self.assertIn("zin_coverage", manifest)
            self.assertIn("sparameter_quality", manifest)
            self.assertIn("geometry_quality", manifest)
            self.assertIn("target_frequency", manifest)
            self.assertEqual(manifest["target_frequency"]["points"], cfg.target.band_points)

    def test_run_sample_dataset_create_only_writes_ground_clearance_audit(self) -> None:
        cfg = default_run_config("1t1t")
        cfg = replace(cfg, emx=replace(cfg.emx, port_mode="single_ended_shield_grounded"))
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = run_sample_dataset(
                run_config=cfg,
                out_dir=Path(tmpdir),
                count=1,
                batch_size=1,
                sampler="lhs",
                seed=6,
                run_emx=False,
            )

            audit_path = Path(tmpdir) / GROUND_CLEARANCE_AUDIT_FILENAME
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["candidate_count"], 1)
            self.assertEqual(audit["pass_count"], 1)
            self.assertEqual(audit["reject_count"], 0)
            self.assertEqual(audit["missing_or_other_count"], 0)
            self.assertEqual(audit["selected"]["status"], "pass_signal_to_shield_clearance")
            self.assertEqual(audit["records"][0]["direct_signal_shield_overlap_area_um2"], 0.0)
            self.assertEqual(audit["records"][0]["signal_shield_clearance_violation_area_um2"], 0.0)
            self.assertEqual(manifest["ground_clearance_audit_path"], str(audit_path.resolve()))
            self.assertEqual(manifest["ground_clearance_quality"]["pass_count"], 1)
