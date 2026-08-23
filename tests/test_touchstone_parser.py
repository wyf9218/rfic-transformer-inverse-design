from tests.rfic_transformer_inverse_design.shared import *

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone


class TouchstoneParserTest(TransformerToolboxTestBase):
    def test_s2p_v1_default_uses_21_12_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "default_order.s2p"
            path.write_text(
                "# GHz S RI R 50\n"
                "5 0.11 0 0.21 0 0.12 0 0.22 0\n",
                encoding="utf-8",
            )

            result = load_touchstone(path)

            self.assertAlmostEqual(result.s_matrix[0, 0, 0].real, 0.11)
            self.assertAlmostEqual(result.s_matrix[0, 1, 0].real, 0.21)
            self.assertAlmostEqual(result.s_matrix[0, 0, 1].real, 0.12)
            self.assertAlmostEqual(result.s_matrix[0, 1, 1].real, 0.22)

    def test_s2p_v2_can_request_12_21_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "natural_order.ts"
            path.write_text(
                "\n".join(
                    [
                        "[Version] 2.1",
                        "# GHz S RI R 50",
                        "[Number of Ports] 2",
                        "[Two-Port Data Order] 12_21",
                        "[Number of Frequencies] 1",
                        "[Network Data]",
                        "5 0.11 0 0.12 0 0.21 0 0.22 0",
                        "[End]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = load_touchstone(path)

            self.assertAlmostEqual(result.s_matrix[0, 0, 0].real, 0.11)
            self.assertAlmostEqual(result.s_matrix[0, 0, 1].real, 0.12)
            self.assertAlmostEqual(result.s_matrix[0, 1, 0].real, 0.21)
            self.assertAlmostEqual(result.s_matrix[0, 1, 1].real, 0.22)

    def test_s2p_to_touchstone_round_trips_default_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "roundtrip.s2p"
            s_matrix = np.asarray(
                [
                    [
                        [0.11 + 0.01j, 0.12 + 0.02j],
                        [0.21 + 0.03j, 0.22 + 0.04j],
                    ]
                ],
                dtype=np.complex128,
            )
            result = SParameterResult(freqs_hz=np.asarray([5.0e9]), s_matrix=s_matrix)
            result.to_touchstone(path)

            parsed = load_touchstone(path)

            self.assertTrue(np.allclose(parsed.s_matrix, s_matrix))

    def test_touchstone_v2_skips_information_block_and_accepts_d_exponents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "info_block.ts"
            path.write_text(
                "\n".join(
                    [
                        "[Version] 2.1",
                        "# GHz S RI R 5D1",
                        "[Number of Ports] 1",
                        "[Begin Information]",
                        "HFSS note: this is descriptive text, not network data.",
                        "[End Information]",
                        "[Number of Frequencies] 1",
                        "[Network Data]",
                        "5D0 1D-1 2D-1",
                        "[End]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = load_touchstone(path)

            self.assertAlmostEqual(result.reference_impedance_ohm, 50.0)
            self.assertAlmostEqual(result.freqs_hz[0], 5.0e9)
            self.assertAlmostEqual(result.s_matrix[0, 0, 0].real, 0.1)
            self.assertAlmostEqual(result.s_matrix[0, 0, 0].imag, 0.2)

    def test_touchstone_v2_lower_matrix_format_expands_symmetric_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lower.ts"
            # Lower order for 3 ports: S11, S21, S22, S31, S32, S33.
            path.write_text(
                "\n".join(
                    [
                        "[Version] 2.1",
                        "# GHz S RI R 50",
                        "[Number of Ports] 3",
                        "[Matrix Format] Lower",
                        "[Network Data]",
                        "5 0.11 0 0.21 0 0.22 0 0.31 0 0.32 0 0.33 0",
                        "[End]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = load_touchstone(path)

            self.assertAlmostEqual(result.s_matrix[0, 0, 0].real, 0.11)
            self.assertAlmostEqual(result.s_matrix[0, 1, 0].real, 0.21)
            self.assertAlmostEqual(result.s_matrix[0, 0, 1].real, 0.21)
            self.assertAlmostEqual(result.s_matrix[0, 2, 1].real, 0.32)
            self.assertAlmostEqual(result.s_matrix[0, 1, 2].real, 0.32)
            self.assertAlmostEqual(result.s_matrix[0, 2, 2].real, 0.33)

    def test_touchstone_v2_upper_matrix_format_expands_symmetric_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "upper.ts"
            # Upper order for 3 ports: S11, S12, S13, S22, S23, S33.
            path.write_text(
                "\n".join(
                    [
                        "[Version] 2.1",
                        "# GHz S RI R 50",
                        "[Number of Ports] 3",
                        "[Matrix Format] Upper",
                        "[Network Data]",
                        "5 0.11 0 0.12 0 0.13 0 0.22 0 0.23 0 0.33 0",
                        "[End]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = load_touchstone(path)

            self.assertAlmostEqual(result.s_matrix[0, 0, 2].real, 0.13)
            self.assertAlmostEqual(result.s_matrix[0, 2, 0].real, 0.13)
            self.assertAlmostEqual(result.s_matrix[0, 1, 2].real, 0.23)
            self.assertAlmostEqual(result.s_matrix[0, 2, 1].real, 0.23)

    def test_unsupported_matrix_format_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad_matrix_format.ts"
            path.write_text(
                "\n".join(
                    [
                        "[Version] 2.1",
                        "# GHz S RI R 50",
                        "[Number of Ports] 2",
                        "[Matrix Format] Sparse",
                        "[Network Data]",
                        "5 0 0 0 0 0 0 0 0",
                        "[End]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsupported \\[Matrix Format\\]"):
                load_touchstone(path)

    def test_touchstone_v2_s4p_keywords_ma_format_and_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "hfss_export.ts"
            pairs: list[str] = []
            for row in range(4):
                for col in range(4):
                    if (row, col) == (0, 1):
                        pairs.extend(["0.2", "90"])
                    elif row == col:
                        pairs.extend(["0.1", "0"])
                    else:
                        pairs.extend(["0", "0"])
            path.write_text(
                "\n".join(
                    [
                        "[Version] 2.1",
                        "# MHz S MA R 75",
                        "[Number of Ports] 4",
                        "[Number of Frequencies] 1",
                        "[Reference] 75 75 75 75",
                        "[Matrix Format] Full",
                        "[Network Data]",
                        "5000 " + " ".join(pairs),
                        "[End]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = load_touchstone(path)

            self.assertEqual(result.num_ports, 4)
            self.assertAlmostEqual(result.freqs_hz[0], 5.0e9)
            self.assertTrue(np.allclose(result.reference_impedance_ohm, np.asarray([75.0, 75.0, 75.0, 75.0])))
            self.assertAlmostEqual(result.s_matrix[0, 0, 1].real, 0.0, places=12)
            self.assertAlmostEqual(result.s_matrix[0, 0, 1].imag, 0.2, places=12)

    def test_s8p_round_trips_full_matrix_and_reference_vector(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "new_power_line_topology.s8p"
            freqs_hz = np.asarray([5.0e9, 5.1e9], dtype=float)
            s_matrix = np.zeros((2, 8, 8), dtype=np.complex128)
            for freq_idx in range(2):
                for row in range(8):
                    for col in range(8):
                        s_matrix[freq_idx, row, col] = (
                            0.001 * (freq_idx + 1)
                            + 0.01 * (row + 1)
                            + 0.0001j * (col + 1)
                        )
            reference = np.asarray([50.0, 50.0, 55.0, 55.0, 60.0, 60.0, 65.0, 65.0], dtype=float)

            SParameterResult(freqs_hz=freqs_hz, s_matrix=s_matrix, reference_impedance_ohm=reference).to_touchstone(path)
            result = load_touchstone(path)

            self.assertEqual(result.num_ports, 8)
            self.assertTrue(np.allclose(result.freqs_hz, freqs_hz))
            self.assertTrue(np.allclose(result.reference_impedance_ohm, reference))
            self.assertTrue(np.allclose(result.s_matrix, s_matrix))

    def test_non_s_parameter_touchstone_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "not_s.s1p"
            path.write_text("# GHz Y RI R 50\n5 0 0\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Unsupported Touchstone parameter type"):
                load_touchstone(path)

    def test_empty_touchstone_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.s4p"
            path.write_text("# GHz S RI R 50\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "No numeric Touchstone data rows"):
                load_touchstone(path)

    def test_incomplete_touchstone_row_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.s2p"
            path.write_text("# GHz S RI R 50\n5.0 0 0 0 0\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Incomplete Touchstone data row"):
                load_touchstone(path)
