from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

from tools.inference import ROOT


@unittest.skipUnless(shutil.which("c++"), "Host compiler required")
class AslCameraInputTests(unittest.TestCase):
    def test_full_view_unrotated_normalized_tensor_matches_training_transform(self):
        try:
            import numpy as np
            from PIL import Image
            from ml.asl.model import transform
            from tools.capture_camera import as_image
        except ImportError:
            self.skipTest("Use the ASL or inference environment")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "input"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "firmware/asl_live/include"),
                            "-I", str(ROOT / "firmware/camera_inference/include"),
                            str(ROOT / "tests/native/asl_camera_input_test.cpp"), "-o", str(executable)],
                           check=True, capture_output=True)
            plane = 480 * 480
            cases = dict(black=bytes(plane * 3), white=bytes([255]) * plane * 3,
                         colors=bytes([255]) * plane + bytes([128]) * plane + bytes(plane),
                         noise=random.Random(2026).randbytes(plane * 3),
                         ramp=bytes((i * 17 + i // 480) & 255 for i in range(plane * 3)))
            for name, payload in cases.items():
                with self.subTest(name=name):
                    result = subprocess.run([str(executable)], input=payload, check=True,
                                            capture_output=True, timeout=10)
                    actual = np.frombuffer(result.stdout, dtype="<f4").reshape(1, 3, 128, 128)
                    image = as_image(payload).resize((128, 128), Image.Resampling.BILINEAR)
                    expected = transform(128)(image).unsqueeze(0).numpy()
                    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
