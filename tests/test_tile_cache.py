import tempfile
import unittest
from unittest.mock import patch

import terrain_pipeline


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class TileCacheTests(unittest.TestCase):
    def test_fetch_tile_bytes_writes_and_reuses_cache(self):
        url = "https://example.test/tile.tif"

        with tempfile.TemporaryDirectory() as cache_dir:
            with patch(
                "terrain_pipeline.request", return_value=FakeResponse(b"tile-bytes")
            ) as request:
                first = terrain_pipeline.fetch_tile_bytes(url, cache_dir=cache_dir)

            with patch("terrain_pipeline.request") as request_again:
                second = terrain_pipeline.fetch_tile_bytes(url, cache_dir=cache_dir)

        self.assertEqual(first, b"tile-bytes")
        self.assertEqual(second, b"tile-bytes")
        request.assert_called_once_with("GET", url, session=None, timeout=120)
        request_again.assert_not_called()


if __name__ == "__main__":
    unittest.main()
