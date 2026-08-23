from tests.rfic_transformer_inverse_design.shared import *


class SParameterResultTest(TransformerToolboxTestBase):
    def test_single_frequency_square_matrix_is_multiport(self) -> None:
        result = SParameterResult(
            freqs_hz=np.asarray([5.0e9]),
            s_matrix=np.asarray([[0.11, 0.12], [0.21, 0.22]], dtype=np.complex128),
        )

        self.assertEqual(result.s_matrix.shape, (1, 2, 2))
        self.assertEqual(result.num_ports, 2)
        self.assertAlmostEqual(result.s21()[0].real, 0.21)

    def test_one_port_frequency_series_still_works(self) -> None:
        result = SParameterResult(
            freqs_hz=np.asarray([5.0e9, 6.0e9]),
            s_matrix=np.asarray([0.1 + 0.2j, 0.2 + 0.3j]),
        )

        self.assertEqual(result.s_matrix.shape, (2, 1, 1))
        self.assertTrue(np.allclose(result.s11(), np.asarray([0.1 + 0.2j, 0.2 + 0.3j])))

    def test_ambiguous_two_dimensional_matrix_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "2D S-matrix input"):
            SParameterResult(freqs_hz=np.asarray([5.0e9, 6.0e9]), s_matrix=np.ones((2, 2)))

    def test_non_square_three_dimensional_matrix_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "port dimensions must be square"):
            SParameterResult(freqs_hz=np.asarray([5.0e9]), s_matrix=np.ones((1, 2, 3)))
