import unittest
from unittest.mock import patch

import geo_assistent


class FakeMapWindow:
    def __init__(self, events):
        self.closed = False
        self.events = events

    def close(self):
        self.closed = True
        self.events.append("close_map")


class GuidedMapLifecycleTests(unittest.TestCase):
    def test_map_closes_only_when_processing_starts(self):
        events = []
        map_window = FakeMapWindow(events)

        def confirm_start(**_kwargs):
            self.assertFalse(map_window.closed)
            events.append("confirm_start")
            return True

        def write_model(*_args, **_kwargs):
            self.assertTrue(map_window.closed)
            events.append("write_model")
            return "terrain.stl"

        with (
            patch("geo_assistent._print_intro"),
            patch("geo_assistent._print_map_help"),
            patch("geo_assistent._open_map", return_value=map_window),
            patch(
                "geo_assistent._prompt_location",
                return_value=(2_600_000.0, 1_200_000.0, "Testpunkt"),
            ),
            patch("geo_assistent._prompt_length_m", side_effect=[100.0, 100.0]),
            patch("geo_assistent._confirm_start", side_effect=confirm_start),
            patch("geo_assistent._write_model", side_effect=write_model),
            patch("geo_assistent._open_model"),
        ):
            self.assertEqual(geo_assistent.main(), 0)

        self.assertEqual(events, ["confirm_start", "close_map", "write_model"])


if __name__ == "__main__":
    unittest.main()
