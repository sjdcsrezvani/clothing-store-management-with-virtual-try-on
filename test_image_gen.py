"""Runnable checks for the virtual try-on image generation service.

Run: python -m unittest test_image_gen
"""
import base64
import io
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from services.image_gen import ImageGenService, MAX_BYTES


def make_image(path, size=(3000, 4000), color=(200, 100, 50)):
    Image.new("RGB", size, color).save(path)


class TestPrep(unittest.TestCase):
    def test_shrinks_large_image_and_stays_small(self):
        svc = ImageGenService()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kid.jpg"
            make_image(p)  # 3000x4000 portrait
            out = svc._prep(str(p))
            img = Image.open(io.BytesIO(out))
            # thumbnail() caps the LONGEST edge at 1024 — for a 3000×4000
            # portrait that yields 768×1024.
            self.assertLessEqual(img.size[0], 1024)
            self.assertLessEqual(img.size[1], 1024)
            self.assertEqual(img.size, (768, 1024))
            self.assertLessEqual(len(out), MAX_BYTES)


class TestBuildPrompt(unittest.TestCase):
    def test_frames_and_styles_base(self):
        svc = ImageGenService()
        p = svc._build_prompt("summer dress")
        self.assertIn("summer dress", p)
        self.assertIn("garment", p)
        self.assertTrue(p.rstrip().endswith("reshape them."))  # MAIN_PROMPT tail

    def test_empty_base_uses_framing_only(self):
        svc = ImageGenService()
        p = svc._build_prompt("")
        self.assertIn("person", p)
        self.assertNotIn(". .", p)


class TestGatewayRequest(unittest.TestCase):
    def _svc_with_handler(self, handler):
        svc = ImageGenService()
        svc.client = httpx.Client(transport=httpx.MockTransport(handler))
        return svc

    def _b64_resp(self):
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"img").decode()}]})

    def test_sends_kid_and_garment_as_image_uploads(self):
        seen = {}
        def handler(request):
            seen["headers"] = request.headers
            seen["body"] = request.content
            return self._b64_resp()
        svc = self._svc_with_handler(handler)
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "kid.jpg"; p2 = Path(d) / "g.jpg"
            make_image(p1, (100, 200)); make_image(p2, (100, 200))
            out = svc.generate("try on", reference_image_paths=[str(p1), str(p2)])
        self.assertEqual(out, b"img")
        self.assertTrue(seen["headers"]["authorization"].startswith("Bearer "))
        body = seen["body"]
        self.assertEqual(body.count(b'name="image[]"'), 2)      # kid + garment
        self.assertIn(b"gpt-image-2", body)
        self.assertIn(b'"size"', body)                           # size field present

    def test_requires_kid_and_garment(self):
        svc = ImageGenService()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kid.jpg"
            make_image(p)
            with self.assertRaises(Exception):
                svc.generate("try on", reference_image_paths=[str(p)])

    def test_extract_data_uri_prefix(self):
        svc = ImageGenService()
        data = {"data": [{"b64_json": "data:image/png;base64," + base64.b64encode(b"abc").decode()}]}
        self.assertEqual(svc._extract_image(data), b"abc")


if __name__ == "__main__":
    unittest.main()