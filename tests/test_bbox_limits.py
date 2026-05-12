import contextlib
import io
import unittest
from unittest.mock import patch

from geo_input import prompt_user_bbox_config, validate_bbox


class BboxLimitTests(unittest.TestCase):
    def test_validate_bbox_can_disable_side_limit(self):
        validate_bbox(
            (2_600_000.0, 1_100_000.0, 2_620_000.0, 1_120_000.0),
            max_side_m=None,
        )

    def test_validate_bbox_keeps_default_side_limit(self):
        with self.assertRaisesRegex(ValueError, "maximal"):
            validate_bbox((2_600_000.0, 1_100_000.0, 2_620_000.0, 1_120_000.0))

    def test_prompt_user_bbox_config_can_disable_side_limit(self):
        answers = iter(
            [
                "2600000 1100000",
                "",
                "6000",
                "6000",
                "",
                "",
                "",
                "",
                "",
            ]
        )
        with (
            patch("builtins.input", lambda _prompt: next(answers)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            selection = prompt_user_bbox_config(
                0.5,
                1000.0,
                1000.0,
                1.0,
                50.0,
                "bilinear",
                "terrain.stl",
                10000.0,
                max_side_m=None,
            )

        min_e, min_n, max_e, max_n = selection.bbox
        self.assertEqual(max_e - min_e, 6000.0)
        self.assertEqual(max_n - min_n, 6000.0)


if __name__ == "__main__":
    unittest.main()
