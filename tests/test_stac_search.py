import contextlib
import io
import unittest
from unittest.mock import patch

from terrain_pipeline import search_tiles


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def feature(href):
    return {
        "assets": {
            "dem": {
                "href": href,
                "title": "swissSURFACE3D raster",
                "type": "image/tiff",
            }
        }
    }


class StacSearchTests(unittest.TestCase):
    def test_search_tiles_follows_next_links(self):
        pages = [
            FakeResponse(
                {
                    "features": [feature("https://example.test/a.tif")],
                    "links": [
                        {
                            "rel": "next",
                            "href": "https://example.test/next",
                            "method": "POST",
                            "body": {"page": 2},
                        }
                    ],
                }
            ),
            FakeResponse(
                {
                    "features": [feature("https://example.test/b.tif")],
                    "links": [],
                }
            ),
        ]
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs.get("json")))
            return pages.pop(0)

        with (
            patch("terrain_pipeline.request", fake_request),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            urls = search_tiles(
                2_600_000.0,
                1_100_000.0,
                2_601_000.0,
                1_101_000.0,
                "collection",
            )

        self.assertEqual(
            urls, ["https://example.test/a.tif", "https://example.test/b.tif"]
        )
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(
            calls[1], ("POST", "https://example.test/next", {"page": 2})
        )


if __name__ == "__main__":
    unittest.main()
