from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

from tools.inference import ROOT


@unittest.skipUnless(shutil.which("c++"), "host compiler required")
class CameraResizeTests(unittest.TestCase):
    def test_full_field_resize_matches_pillow_bytes(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Use the ASL environment for Pillow checks")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "resize"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "firmware/camera_inference/include"),
                            str(ROOT / "tests/native/camera_resize_test.cpp"), "-o", str(executable)],
                           check=True, capture_output=True)
            plane = 480 * 480
            cases = {
                "black": bytes(plane * 3),
                "white": bytes([255]) * plane * 3,
                "colors": bytes([255]) * plane + bytes([128]) * plane + bytes(plane),
                "noise": random.Random(2026).randbytes(plane * 3),
                "ramp": bytes((i * 17 + i // 480) & 255 for i in range(plane * 3)),
                "checkerboard": bytes(255 * ((i % 480 + i // 480) % 2) for i in range(plane)) * 3,
            }
            # Edge/corner impulses exercise clipped/renormalized filter support.
            for position in (0, 479, plane - 480, plane - 1, plane // 2 + 240):
                impulse = bytearray(plane * 3)
                impulse[position] = 255
                impulse[plane + position] = 128
                cases[f"impulse-{position}"] = bytes(impulse)
            for name, payload in cases.items():
                with self.subTest(name=name):
                    image = Image.merge("RGB", tuple(Image.frombytes("L", (480, 480),
                        payload[p * plane:(p + 1) * plane]) for p in range(3)))
                    expected = b"".join(channel.tobytes() for channel in image.resize(
                        (128, 128), Image.Resampling.BILINEAR).split())
                    result = subprocess.run([str(executable)], input=payload, check=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.stdout, expected)


if __name__ == "__main__":
    unittest.main()
