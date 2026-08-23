from rfic_transformer_inverse_design.sim.emx import render


class _FakeAxis:
    def __init__(self) -> None:
        self.text_calls: list[tuple[object, ...]] = []
        self.patches: list[object] = []

    def add_patch(self, patch) -> None:
        self.patches.append(patch)

    def text(self, *args, **kwargs) -> None:
        self.text_calls.append((args, kwargs))


def test_draw_layout_can_hide_labels_in_main_preview() -> None:
    axis = _FakeAxis()

    render._draw_layout(
        axis,
        render_cells=[],
        label_positions={"P001": (0.0, 0.0), "PVDD_TOP": (1.0, 1.0)},
        port_boxes=[],
        polygon_cls=lambda *args, **kwargs: None,
        rectangle_cls=lambda *args, **kwargs: None,
        show_labels=False,
        show_port_boxes=False,
    )

    assert axis.text_calls == []


def test_draw_layout_keeps_optional_label_rendering_for_debug_views() -> None:
    axis = _FakeAxis()

    render._draw_layout(
        axis,
        render_cells=[],
        label_positions={"P001": (0.0, 0.0), "PVDD_TOP": (1.0, 1.0)},
        port_boxes=[],
        polygon_cls=lambda *args, **kwargs: None,
        rectangle_cls=lambda *args, **kwargs: None,
        show_labels=True,
        show_port_boxes=False,
    )

    assert len(axis.text_calls) == 2
