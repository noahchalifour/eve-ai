"""The grid rules. The client mirrors these; the server re-checks every save."""
from eve.dashboards import layout


def test_pack_fills_rows_first_fit_in_order():
    spots = layout.pack([(4, 2), (2, 2), (2, 2), (2, 4)], 4)
    assert spots == [(0, 0), (0, 2), (2, 2), (0, 4)]


def test_pack_puts_a_small_tile_into_a_gap_left_by_a_tall_one():
    spots = layout.pack([(2, 4), (2, 2), (2, 2)], 4)
    assert spots == [(0, 0), (2, 0), (2, 2)]


def test_pack_clamps_a_wide_tile_to_the_grid():
    assert layout.pack([(4, 2)], 4) == [(0, 0)]


STORED = [
    {"resourceId": "a", "sizes": ["2x2", "4x2"], "x": 0, "y": 0, "w": 2, "h": 2},
    {"resourceId": "b", "sizes": ["4x2"], "x": 0, "y": 2, "w": 4, "h": 2},
]


def test_a_move_and_an_allowed_resize_are_accepted():
    result = layout.validate(
        [{"resourceId": "b", "x": 0, "y": 0, "w": 4, "h": 2},
         {"resourceId": "a", "x": 0, "y": 2, "w": 4, "h": 2}], STORED, 4)
    assert [(t["resourceId"], t["y"], t["w"]) for t in result] == [("b", 0, 4), ("a", 2, 4)]
    # The server's sizes are kept, whatever the client sent.
    assert result[1]["sizes"] == ["2x2", "4x2"]


def test_dropping_a_tile_is_accepted():
    result = layout.validate([{"resourceId": "a", "x": 0, "y": 0, "w": 2, "h": 2}], STORED, 4)
    assert len(result) == 1


def test_a_size_the_widget_does_not_allow_is_refused():
    assert "not a size" in layout.validate(
        [{"resourceId": "b", "x": 0, "y": 0, "w": 2, "h": 2}], STORED, 4)


def test_overlap_is_refused():
    assert "overlaps" in layout.validate(
        [{"resourceId": "a", "x": 0, "y": 0, "w": 2, "h": 2},
         {"resourceId": "b", "x": 0, "y": 1, "w": 4, "h": 2}], STORED, 4)


def test_leaving_the_grid_is_refused():
    assert "overlaps or leaves" in layout.validate(
        [{"resourceId": "a", "x": 3, "y": 0, "w": 2, "h": 2}], STORED, 4)


def test_a_tile_the_dashboard_does_not_have_is_refused():
    """A client cannot smuggle a widget onto the dashboard by id."""
    assert "does not have" in layout.validate(
        [{"resourceId": "zzz", "x": 0, "y": 0, "w": 2, "h": 2}], STORED, 4)


def test_a_duplicate_tile_is_refused():
    placement = {"resourceId": "a", "x": 0, "y": 0, "w": 2, "h": 2}
    assert "twice" in layout.validate([placement, {**placement, "x": 2}], STORED, 4)


def test_boolean_coordinates_are_refused():
    assert "integers" in layout.validate(
        [{"resourceId": "a", "x": True, "y": 0, "w": 2, "h": 2}], STORED, 4)


def test_device_ids_are_bounded():
    assert layout.valid_device_id("d3v1ce-ID_12345")
    assert not layout.valid_device_id("short")
    assert not layout.valid_device_id("has spaces in it")
    assert not layout.valid_device_id(None)
