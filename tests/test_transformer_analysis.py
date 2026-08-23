from tests.rfic_transformer_inverse_design.shared import *


class TransformerAnalysisTest(TransformerToolboxTestBase):
    def test_explicit_s8p_port_pairs_recover_lumped_metrics(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        z_diff = s_to_z(diff.s_matrix, z0=target.differential_reference_impedance_ohm)
        transform = np.zeros((8, 2), dtype=np.complex128)
        transform[0, 0] = 1.0
        transform[1, 0] = -1.0
        transform[6, 1] = 1.0
        transform[7, 1] = -1.0
        z8 = 0.25 * np.einsum("ai,fij,bj->fab", transform, z_diff, transform)
        s8 = z_to_s(z8, z0=50.0)
        sparams = SParameterResult(freqs_hz=freqs, s_matrix=s8, reference_impedance_ohm=50.0)

        metrics, round_trip_diff, extracted_z = extract_transformer_metrics_from_single_ended_pairs(
            sparams,
            target,
            ((0, 1), (6, 7)),
        )

        self.assertTrue(np.allclose(extracted_z, z_diff, atol=1e-9, rtol=1e-8))
        self.assertAlmostEqual(metrics.lp_h, target.lp_h, delta=target.lp_h * 1e-6)
        self.assertAlmostEqual(metrics.ls_h, target.ls_h, delta=target.ls_h * 1e-6)
        self.assertAlmostEqual(metrics.k, target.k_target, delta=1e-6)
        self.assertAlmostEqual(metrics.q_primary, 18.0, delta=1e-6)
        self.assertAlmostEqual(metrics.q_secondary, 16.0, delta=1e-6)
        self.assertEqual(round_trip_diff.num_ports, 2)

    def test_prepare_touchstone_uses_explicit_s8p_pairs_without_legacy_reduction(self) -> None:
        from rfic_transformer_inverse_design.execution.zeus_cadence import prepare_transformer_touchstone_result

        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        z_diff = s_to_z(diff.s_matrix, z0=target.differential_reference_impedance_ohm)
        transform = np.zeros((8, 2), dtype=np.complex128)
        transform[0, 0] = 1.0
        transform[1, 0] = -1.0
        transform[6, 1] = 1.0
        transform[7, 1] = -1.0
        z8 = 0.25 * np.einsum("ai,fij,bj->fab", transform, z_diff, transform)
        raw_result = SParameterResult(freqs_hz=freqs, s_matrix=z_to_s(z8, z0=50.0), reference_impedance_ohm=50.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "emx.s8p"
            prepared = prepare_transformer_touchstone_result(
                raw_result=raw_result,
                target=target,
                raw_touchstone_path=raw_path,
                out_dir=Path(tmpdir),
                differential_port_pairs=((0, 1), (6, 7)),
            )

        self.assertEqual(prepared["raw_touchstone_path"], raw_path)
        self.assertEqual(prepared["touchstone_path"], raw_path)
        self.assertIsNone(prepared["reduced_touchstone_path"])
        self.assertEqual(prepared["single_result"].num_ports, 8)
        self.assertEqual(prepared["differential_result"].num_ports, 2)
        self.assertAlmostEqual(prepared["metrics"].k, target.k_target, delta=1e-6)

    def test_multiport_pair_projection_rejects_ambiguous_or_invalid_pairs(self) -> None:
        z8 = np.zeros((1, 8, 8), dtype=np.complex128)

        with self.assertRaisesRegex(ValueError, "four distinct ports"):
            multiport_single_ended_to_differential_z(z8, ((0, 1), (1, 2)))
        with self.assertRaisesRegex(ValueError, "exceed available port count"):
            multiport_single_ended_to_differential_z(z8, ((0, 1), (6, 8)))

    def test_extractor_recovers_lumped_metrics_from_synthetic_network(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        single = differential_2port_to_4port_s(freqs, diff.s_matrix)

        metrics, round_trip_diff, _ = extract_transformer_metrics(single, target)

        self.assertAlmostEqual(metrics.lp_h, target.lp_h, delta=target.lp_h * 1e-6)
        self.assertAlmostEqual(metrics.ls_h, target.ls_h, delta=target.ls_h * 1e-6)
        self.assertAlmostEqual(metrics.k, target.k_target, delta=1e-6)
        self.assertAlmostEqual(metrics.q_primary, 18.0, delta=1e-6)
        self.assertAlmostEqual(metrics.q_secondary, 16.0, delta=1e-6)
        self.assertTrue(np.allclose(round_trip_diff.s_matrix, diff.s_matrix, atol=1e-9, rtol=1e-8))
    def test_differential_extractor_recovers_lumped_metrics_from_direct_2port_result(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)

        metrics, round_trip_diff, _ = extract_transformer_metrics_from_differential(diff, target)

        self.assertAlmostEqual(metrics.lp_h, target.lp_h, delta=target.lp_h * 1e-6)
        self.assertAlmostEqual(metrics.ls_h, target.ls_h, delta=target.ls_h * 1e-6)
        self.assertAlmostEqual(metrics.k, target.k_target, delta=1e-6)
        self.assertAlmostEqual(metrics.q_primary, 18.0, delta=1e-6)
        self.assertAlmostEqual(metrics.q_secondary, 16.0, delta=1e-6)
        self.assertTrue(np.allclose(round_trip_diff.s_matrix, diff.s_matrix, atol=1e-12, rtol=1e-10))
    def test_extractor_secondary_polarity_convention_is_explicit(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        z_diff = s_to_z(diff.s_matrix, z0=target.differential_reference_impedance_ohm)

        from rfic_transformer_inverse_design.analysis.extraction import _mixed_mode_current_matrix, _mixed_mode_voltage_matrix

        a_v_inv = np.linalg.inv(_mixed_mode_voltage_matrix())
        a_i = _mixed_mode_current_matrix()
        legacy_internal_z = np.empty((z_diff.shape[0], 4, 4), dtype=np.complex128)
        common = np.eye(2, dtype=np.complex128) * 1.0e6
        for idx, z_f in enumerate(z_diff):
            z_mixed = np.zeros((4, 4), dtype=np.complex128)
            z_mixed[:2, :2] = z_f
            z_mixed[2:, 2:] = common
            legacy_internal_z[idx] = a_v_inv @ z_mixed @ a_i

        physical_external_z = legacy_internal_z[:, (0, 1, 3, 2), :][:, :, (0, 1, 3, 2)]
        single = SParameterResult(
            freqs_hz=freqs,
            s_matrix=z_to_s(physical_external_z, z0=50.0),
        )

        swapped_secondary = SParameterResult(
            freqs_hz=single.freqs_hz,
            s_matrix=single.s_matrix[:, (0, 1, 3, 2), :][:, :, (0, 1, 3, 2)],
        )

        metrics, _, _ = extract_transformer_metrics(single, target)
        swapped_metrics, _, _ = extract_transformer_metrics(swapped_secondary, target)

        self.assertGreater(metrics.k, 0.0)
        self.assertLess(swapped_metrics.k, 0.0)
    def test_objective_prefers_closer_match_and_gates_q_reward(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        metrics, _, _ = extract_transformer_metrics(differential_2port_to_4port_s(freqs, diff.s_matrix), target)

        close_score = score_transformer_result(target, metrics, diff)
        far_metrics = replace(
            metrics,
            lp_h=target.lp_h * 1.20,
            ls_h=target.ls_h * 0.82,
            k=target.k_target * 0.70,
        )
        far_score = score_transformer_result(target, far_metrics, diff)
        self.assertLess(close_score.total_cost, far_score.total_cost)

        low_q_metrics = replace(metrics, q_primary=8.0, q_secondary=7.5)
        high_q_metrics = replace(metrics, q_primary=22.0, q_secondary=21.0)
        self.assertLess(
            score_transformer_result(target, high_q_metrics, diff).total_cost,
            score_transformer_result(target, low_q_metrics, diff).total_cost,
        )

        far_low_q = replace(far_metrics, q_primary=8.0, q_secondary=7.5)
        far_high_q = replace(far_metrics, q_primary=22.0, q_secondary=21.0)
        far_low_score = score_transformer_result(target, far_low_q, diff)
        far_high_score = score_transformer_result(target, far_high_q, diff)
        self.assertEqual(far_low_score.q_reward, 0.0)
        self.assertEqual(far_high_score.q_reward, 0.0)
        self.assertAlmostEqual(far_low_score.total_cost, far_high_score.total_cost, delta=1e-12)
        self.assertGreaterEqual(close_score.total_cost, 0.0)
        self.assertGreaterEqual(far_score.total_cost, 0.0)
    def test_objective_requires_each_lp_ls_k_error_to_be_close_before_q_matters(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        metrics, _, _ = extract_transformer_metrics(differential_2port_to_4port_s(freqs, diff.s_matrix), target)

        gated_metrics = replace(
            metrics,
            lp_h=target.lp_h * 1.06,
            ls_h=target.ls_h * 1.00,
            k=target.k_target * 1.00,
        )
        low_q = replace(gated_metrics, q_primary=8.0, q_secondary=7.5)
        high_q = replace(gated_metrics, q_primary=22.0, q_secondary=21.0)

        low_q_score = score_transformer_result(target, low_q, diff)
        high_q_score = score_transformer_result(target, high_q, diff)

        self.assertEqual(low_q_score.q_reward, 0.0)
        self.assertEqual(high_q_score.q_reward, 0.0)
        self.assertAlmostEqual(low_q_score.total_cost, high_q_score.total_cost, delta=1e-12)
        self.assertGreaterEqual(low_q_score.total_cost, 0.0)
        self.assertGreaterEqual(high_q_score.total_cost, 0.0)
    def test_objective_q_reward_activates_once_all_targets_are_within_five_percent(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        metrics, _, _ = extract_transformer_metrics(differential_2port_to_4port_s(freqs, diff.s_matrix), target)

        gated_on_metrics = replace(
            metrics,
            lp_h=target.lp_h * 1.049,
            ls_h=target.ls_h * 0.951,
            k=target.k_target * 1.049,
        )
        low_q = replace(gated_on_metrics, q_primary=8.0, q_secondary=7.5)
        high_q = replace(gated_on_metrics, q_primary=22.0, q_secondary=21.0)

        low_q_score = score_transformer_result(target, low_q, diff)
        high_q_score = score_transformer_result(target, high_q, diff)

        self.assertGreater(low_q_score.q_reward, 0.0)
        self.assertGreater(high_q_score.q_reward, low_q_score.q_reward)
        self.assertLess(high_q_score.total_cost, low_q_score.total_cost)
    def test_target_spec_no_longer_carries_ql_target(self) -> None:
        target = default_target_spec("1t1t")
        self.assertFalse(hasattr(target, "ql_target"))
    def test_objective_uses_q_as_nonnegative_multiplicative_improvement(self) -> None:
        target = default_target_spec("1t1t")
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        metrics, _, _ = extract_transformer_metrics(differential_2port_to_4port_s(freqs, diff.s_matrix), target)

        low_q_metrics = replace(metrics, q_primary=8.0, q_secondary=7.5)
        high_q_metrics = replace(metrics, q_primary=22.0, q_secondary=21.0)

        low_q_score = score_transformer_result(target, low_q_metrics, diff)
        high_q_score = score_transformer_result(target, high_q_metrics, diff)

        self.assertGreater(low_q_score.q_reward, 0.0)
        self.assertGreater(high_q_score.q_reward, low_q_score.q_reward)
        self.assertLess(high_q_score.total_cost, low_q_score.total_cost)
        self.assertGreaterEqual(low_q_score.total_cost, 0.0)
        self.assertGreaterEqual(high_q_score.total_cost, 0.0)
    def test_objective_can_match_explicit_q_targets(self) -> None:
        target = replace(
            default_target_spec("1t1t"),
            q_target_mode="target",
            q_primary_target=18.0,
            q_secondary_target=16.0,
        )
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        metrics, _, _ = extract_transformer_metrics(differential_2port_to_4port_s(freqs, diff.s_matrix), target)

        matched_score = score_transformer_result(target, metrics, diff)
        off_target_metrics = replace(metrics, q_primary=11.0, q_secondary=24.0)
        off_target_score = score_transformer_result(target, off_target_metrics, diff)

        self.assertAlmostEqual(matched_score.q_target_term, 0.0, delta=1e-9)
        self.assertLess(matched_score.total_cost, off_target_score.total_cost)
        self.assertGreater(off_target_score.q_target_term, 0.0)
    def test_explicit_q_targets_are_gated_by_lp_ls_k_match(self) -> None:
        target = replace(
            default_target_spec("1t1t"),
            q_target_mode="target",
            q_primary_target=18.0,
            q_secondary_target=16.0,
        )
        freqs = np.linspace(*target.band_edges_hz(), target.band_points)
        diff = build_lumped_transformer_sparameters(freqs, target, q_primary=18.0, q_secondary=16.0)
        metrics, _, _ = extract_transformer_metrics(differential_2port_to_4port_s(freqs, diff.s_matrix), target)

        gated_metrics = replace(
            metrics,
            lp_h=target.lp_h * 1.08,
            q_primary=8.0,
            q_secondary=26.0,
        )
        different_q_metrics = replace(gated_metrics, q_primary=25.0, q_secondary=9.0)

        first_score = score_transformer_result(target, gated_metrics, diff)
        second_score = score_transformer_result(target, different_q_metrics, diff)

        self.assertAlmostEqual(first_score.q_target_term, 0.0, delta=1e-12)
        self.assertAlmostEqual(second_score.q_target_term, 0.0, delta=1e-12)
        self.assertAlmostEqual(first_score.total_cost, second_score.total_cost, delta=1e-12)
