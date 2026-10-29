"""Runnable checks for the virtual try-on image generation service.

Run: python -m unittest test_image_gen
"""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from services.image_gen import ImageGenService, ImageGenerationError, MAX_BYTES


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
    def _svc_with_handler(self, handler, provider="gapgpt", model="gpt-image-2"):
        svc = ImageGenService(api_key="test-key", provider=provider, model=model)
        svc.client = httpx.Client(transport=httpx.MockTransport(handler))
        return svc

    def _b64_resp(self):
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"img").decode()}]})

    def _paths(self, d):
        p1 = Path(d) / "kid.jpg"
        p2 = Path(d) / "g.jpg"
        make_image(p1, (100, 200))
        make_image(p2, (100, 200))
        return [str(p1), str(p2)]

    def test_sends_kid_and_garment_as_image_uploads(self):
        seen = {}
        def handler(request):
            seen["headers"] = request.headers
            seen["body"] = request.content
            return self._b64_resp()
        svc = self._svc_with_handler(handler)
        with tempfile.TemporaryDirectory() as d:
            out = svc.generate("try on", reference_image_paths=self._paths(d))
        self.assertEqual(out, b"img")
        self.assertTrue(seen["headers"]["authorization"].startswith("Bearer "))
        body = seen["body"]
        self.assertEqual(body.count(b'name="image[]"'), 2)      # kid + garment
        self.assertIn(b"gpt-image-2", body)
        self.assertIn(b'"size"', body)                           # size field present
        self.assertIn(b'"quality"', body)                        # cheapest GPT tier
        self.assertNotIn(b"response_format", body)               # 400s GPT Image

    def test_seedream_sends_json_with_data_uri_array(self):
        seen = {}
        def handler(request):
            seen["content_type"] = request.headers["content-type"]
            seen["json"] = json.loads(request.content)
            return self._b64_resp()
        svc = self._svc_with_handler(handler, "avalai", "seedream-5-0-260128")
        with tempfile.TemporaryDirectory() as d:
            out = svc.generate("try on", reference_image_paths=self._paths(d))
        self.assertEqual(out, b"img")
        self.assertTrue(seen["content_type"].startswith("application/json"))
        payload = seen["json"]
        self.assertEqual(payload["size"], "1K")
        self.assertEqual(len(payload["image"]), 2)
        self.assertTrue(payload["image"][0].startswith("data:image/jpeg;base64,"))
        self.assertEqual(payload["response_format"], "b64_json")
        self.assertNotIn("quality", payload)

    def test_muse_sends_json_image_objects_and_no_size(self):
        seen = {}
        def handler(request):
            seen["json"] = json.loads(request.content)
            return self._b64_resp()
        svc = self._svc_with_handler(handler, "avalai", "muse-image-1.0")
        with tempfile.TemporaryDirectory() as d:
            svc.generate("try on", reference_image_paths=self._paths(d))
        payload = seen["json"]
        self.assertEqual(len(payload["images"]), 2)
        self.assertTrue(payload["images"][0]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("size", payload)

    def test_content_policy_is_classified_and_logged(self):
        from services import tryon_debug
        def handler(request):
            return httpx.Response(400, json={"error": {
                "code": "moderation_blocked", "message": "blocked by policy"}})
        svc = self._svc_with_handler(handler)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ImageGenerationError) as caught:
                svc.generate("try on", reference_image_paths=self._paths(d), job_id=987654)
        error = caught.exception
        self.assertEqual(error.category, "content_policy")
        self.assertEqual(error.code, "moderation_blocked")
        self.assertIn("خط‌مشی محتوا", str(error))
        attempt = tryon_debug.latest_for_job(987654)
        self.assertIsNotNone(attempt)
        self.assertFalse(attempt["ok"])
        self.assertEqual(attempt["fields"], "size=1024x1024, quality=low, output_format=jpeg, n=1, images=2")
        self.assertGreaterEqual(attempt["total_ms"], 0)

    def test_requires_kid_and_garment(self):
        svc = ImageGenService(api_key="test-key", provider="gapgpt", model="gpt-image-2")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kid.jpg"
            make_image(p)
            with self.assertRaises(ImageGenerationError):
                svc.generate("try on", reference_image_paths=[str(p)])

    def test_extract_data_uri_prefix(self):
        svc = ImageGenService()
        data = {"data": [{"b64_json": "data:image/png;base64," + base64.b64encode(b"abc").decode()}]}
        self.assertEqual(svc._extract_image(data), b"abc")

    def test_text_only_200_is_a_refusal(self):
        svc = ImageGenService()
        with self.assertRaises(ImageGenerationError) as caught:
            svc._extract_image({"choices": [{"message": {"content": "I can't help with that."}}]})
        self.assertEqual(caught.exception.category, "refusal")

    def test_safety_finish_reason_is_content_policy(self):
        svc = ImageGenService()
        with self.assertRaises(ImageGenerationError) as caught:
            svc._extract_image({"candidates": [{"finishReason": "IMAGE_SAFETY"}]})
        self.assertEqual(caught.exception.category, "content_policy")
        self.assertEqual(caught.exception.code, "IMAGE_SAFETY")

    def test_policy_code_in_a_200_body_is_content_policy(self):
        svc = ImageGenService()
        with self.assertRaises(ImageGenerationError) as caught:
            svc._extract_image({"code": "OutputImageSensitiveContentDetected",
                                "message": "blocked"})
        self.assertEqual(caught.exception.category, "content_policy")
        self.assertEqual(caught.exception.code, "OutputImageSensitiveContentDetected")

    def test_seedream_sensitive_code_is_content_policy(self):
        from services.image_gen import classify_response
        category, code = classify_response(
            400, '{"error":{"code":"InputImageSensitiveContentDetected.PrivacyInformation"}}')
        self.assertEqual(category, "content_policy")
        self.assertIn("InputImageSensitiveContentDetected", code)


if __name__ == "__main__":
    unittest.main()
