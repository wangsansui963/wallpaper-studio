#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline verification cases - no network access, no third-party packages."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wallpaper_studio as ws  # noqa: E402

SPEC_PATH = Path(__file__).resolve().parents[1] / "gallery_spec.json"
SPEC = ws.load_spec(SPEC_PATH)

SAMPLE = {
    "src": {"rawSrc": "https://infinitypro-img.infinitynewtab.com/findaphoto/bigLink/15228.jpg"},
    "colors": ["#909048"], "rate": 0, "like": 502,
    "_id": "57ca8fc2d54a684cdf14dcfe", "imgId": "15228",
    "dimensions": "2048px × 1143px", "source": "Unsplash",
}


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200,
                 ctype: str = "image/jpeg") -> None:
        self._data = data
        self.status = status
        self.headers = {"Content-Type": ctype}
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class TestProtocol(unittest.TestCase):
    def test_spec_has_required_sections(self):
        for key in ("endpoints", "sources", "pagination", "item_schema",
                    "image_transform", "daily"):
            self.assertIn(key, SPEC)
        self.assertEqual(len(SPEC["sources"]), 14)

    def test_source_all_maps_to_none(self):
        all_src = next(s for s in SPEC["sources"] if s["value"] == "all")
        self.assertIsNone(all_src["query_value"])

    def test_convert_url_with_webp(self):
        self.assertEqual(
            ws.convert_url("https://cdn/x.jpg", 1920, True),
            "https://cdn/x.jpg?imageView2/2/w/1920/format/webp/interlace/1")

    def test_convert_url_without_webp(self):
        self.assertEqual(
            ws.convert_url("https://cdn/x.jpg", 800, False),
            "https://cdn/x.jpg?imageView2/2/w/800/interlace/1")

    def test_parse_item(self):
        w = ws.parse_item(SAMPLE, page=3)
        self.assertEqual(w.id, "57ca8fc2d54a684cdf14dcfe")
        self.assertEqual(w.img_id, "15228")
        self.assertEqual(w.source, "Unsplash")
        self.assertEqual(w.like, 502)
        self.assertEqual(w.page, 3)
        self.assertTrue(w.raw_url.startswith("https://"))

    def test_roundtrip_dict(self):
        w = ws.parse_item(SAMPLE)
        self.assertEqual(ws.wallpaper_from_dict(ws.asdict(w)), w)

    def test_dedupe_key(self):
        a = ws.parse_item(SAMPLE)
        b = ws.parse_item({**SAMPLE, "imgId": "other"})
        self.assertEqual(ws.dedupe_key(a), ws.dedupe_key(b))

    def test_safe_filename_has_no_traversal(self):
        evil = ws.Wallpaper(id="../../etc/passwd", img_id="x",
                            source="../bad", raw_url="https://cdn/x.jpg")
        name = ws.safe_filename(evil, ".jpg")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertTrue(name.endswith(".jpg"))

    def test_daily_infinity_url(self):
        self.assertEqual(
            ws.daily_url(SPEC, "infinity", datetime(2026, 3, 7)),
            "https://wallpaper.infinitynewtab.com/InfinityWallpaper/3_7.jpg")

    def test_daily_bing_url(self):
        self.assertEqual(
            ws.daily_url(SPEC, "bing", datetime(2026, 3, 7), "abc123"),
            "https://img.infinitynewtab.com/bingWallpaper/bg.jpg?md5=abc123")

    def test_guess_ext(self):
        self.assertEqual(ws.guess_ext("https://cdn/a.PNG"), ".png")
        self.assertEqual(ws.guess_ext("https://cdn/a"), ".jpg")
        self.assertEqual(ws.guess_ext("https://cdn/a.jpg", True), ".webp")


class TestGalleryIteration(unittest.TestCase):
    def test_pagination_and_dedupe(self):
        page0 = [SAMPLE, {**SAMPLE, "imgId": "other-img"}]
        page1 = [{**SAMPLE, "_id": "new-one", "imgId": "999"}]
        pages = {
            0: (page0, {"count": 3, "totalPages": 2, "nextPage": 1}),
            1: (page1, {"count": 3, "totalPages": 2, "nextPage": 2}),
        }
        with mock.patch.object(ws, "fetch_list_page",
                               side_effect=lambda spec, page, **kw: pages[page]):
            items = list(ws.iter_gallery(SPEC, pages=2, delay=0))
        # page0 has two records sharing the same _id -> de-duplicated to 1
        self.assertEqual(len(items), 2)
        self.assertEqual({w.id for w in items},
                         {"57ca8fc2d54a684cdf14dcfe", "new-one"})

    def test_max_items_stops_early(self):
        many = [{**SAMPLE, "_id": f"id-{i}"} for i in range(10)]
        with mock.patch.object(ws, "fetch_list_page",
                               return_value=(many, {"totalPages": 1, "nextPage": 1})):
            items = list(ws.iter_gallery(SPEC, max_items=3, delay=0))
        self.assertEqual(len(items), 3)


class TestDownload(unittest.TestCase):
    def test_download_one_success(self):
        w = ws.parse_item(SAMPLE)
        payload = b"\xff\xd8\xff" + b"x" * 100
        with tempfile.TemporaryDirectory() as td, \
                mock.patch("urllib.request.urlopen",
                           return_value=FakeResponse(payload)):
            status, name, size = ws.download_one(w, Path(td))
            self.assertEqual(status, "ok")
            self.assertEqual(int(size), len(payload))
            self.assertEqual((Path(td) / name).read_bytes(), payload)

    def test_download_one_skips_existing(self):
        w = ws.parse_item(SAMPLE)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / ws.safe_filename(w, ".jpg")
            dest.write_bytes(b"already")
            status, _, size = ws.download_one(w, Path(td))
            self.assertEqual(status, "skipped")
            self.assertEqual(int(size), 7)

    def test_download_one_rejects_non_https(self):
        w = ws.Wallpaper("id", "img", "src", "http://example.com/a.jpg")
        with tempfile.TemporaryDirectory() as td:
            status, _, _ = ws.download_one(w, Path(td))
            self.assertEqual(status, "failed")

    def test_download_one_rejects_non_image(self):
        w = ws.parse_item(SAMPLE)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch("urllib.request.urlopen",
                           return_value=FakeResponse(b"<html>", 200, "text/html")):
            status, _, info = ws.download_one(w, Path(td), retries=0)
            self.assertEqual(status, "failed")
            self.assertIn("content-type", info)


class TestManifest(unittest.TestCase):
    def test_write_json_and_csv(self):
        items = [ws.parse_item(SAMPLE)]
        with tempfile.TemporaryDirectory() as td:
            j = ws.write_manifest(Path(td), items, "json")
            c = ws.write_manifest(Path(td), items, "csv")
            data = json.loads(j.read_text(encoding="utf-8"))
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["items"][0]["source"], "Unsplash")
            self.assertIn("raw_url", c.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
