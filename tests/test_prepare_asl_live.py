from pathlib import Path
import json
import tempfile
from unittest import mock

from tools import inference
from tools import prepare_asl_live as live
from tools import prepare_camera_inference as capture
import test_prepare_camera_inference as guards


class PrepareLiveTests(guards.PrepareCameraInferenceTests):
    def setUp(self):
        self.engine = live.package_engine()
        patch = mock.patch.object(guards, "prepare", self.engine)
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        super().setUp()

    def test_no_implicit_experimental_acknowledgement(self):
        with mock.patch.object(live, "package_engine") as engine:
            with self.assertRaises(inference.SetupError):
                live.prepare("/unused", Path("/unused"), True, False)
            engine.assert_not_called()

    def test_private_engine_keeps_existing_capture_selection_unchanged(self):
        before = (capture.PROGRAM, capture.IMAGE_NAME, capture.check_build)
        engine = live.package_engine()
        self.assertNotEqual(engine.PROGRAM, capture.PROGRAM)
        self.assertIs(engine.check_build, live.live.check_build)
        self.assertEqual(before, (capture.PROGRAM, capture.IMAGE_NAME, capture.check_build))
        self.assertEqual(live.PROGRAM.name, "asl-static-programming-v1")
        self.assertNotEqual(live.PROGRAM, inference.WORK / "asl-live-programming-v1")

    def test_smoke_build_cannot_be_used_as_stored_image_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(dict(target="DevKit-E8-HP", build="debug")))
            with mock.patch.object(live.live, "WORKSPACE", root), mock.patch.object(live.live, "export_info") as check:
                with self.assertRaises(inference.SetupError):
                    live.live.check_build(Path("unused"))
                check.assert_not_called()
