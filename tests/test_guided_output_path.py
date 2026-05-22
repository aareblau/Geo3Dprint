import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import geo_assistent


class GuidedOutputPathTests(unittest.TestCase):
    def test_model_name_is_saved_as_stl_in_desktop_models_folder(self):
        with tempfile.TemporaryDirectory() as home:
            with patch("geo_assistent._desktop_dir", return_value=Path(home) / "Desktop"):
                path = geo_assistent._desktop_output_path("Mein Modell")

            self.assertEqual(path, Path(home) / "Desktop" / "Modelle" / "Mein_Modell.stl")
            self.assertTrue((Path(home) / "Desktop" / "Modelle").is_dir())

    def test_model_name_sanitizes_windows_filename_characters(self):
        with tempfile.TemporaryDirectory() as home:
            with patch("geo_assistent._desktop_dir", return_value=Path(home) / "Desktop"):
                path = geo_assistent._desktop_output_path("Matterhorn: Nord/West.stl")

        self.assertEqual(path, Path(home) / "Desktop" / "Modelle" / "Matterhorn_Nord_West.stl")

    def test_model_name_is_required(self):
        with self.assertRaises(ValueError):
            geo_assistent._desktop_output_path("   ")

    def test_prompt_rejects_existing_desktop_file(self):
        with tempfile.TemporaryDirectory() as home:
            desktop = Path(home) / "Desktop"
            models = desktop / "Modelle"
            models.mkdir(parents=True)
            (models / "Bestehend.stl").write_bytes(b"old")
            answers = iter(["Bestehend", "Neu"])

            with (
                patch("geo_assistent._desktop_dir", return_value=desktop),
                patch("builtins.input", lambda _prompt: next(answers)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                path = geo_assistent._prompt_model_output_path()

        self.assertEqual(path, desktop / "Modelle" / "Neu.stl")


if __name__ == "__main__":
    unittest.main()
