from pathlib import Path
import json
import tempfile
from unittest import mock
from tools import prepare_camera_collect as collect, prepare_camera_inference as capture
import test_prepare_camera_inference as guards


class CollectPackageTests(guards.PrepareCameraInferenceTests):
    def setUp(self):
        self.engine=collect.package_engine()
        patch=mock.patch.object(guards,'prepare',self.engine)
        patch.__enter__();self.addCleanup(patch.__exit__,None,None,None)
        super().setUp()

    def test_collection_engine_keeps_live_and_capture_pointers_unchanged(self):
        from tools import prepare_asl_live as live
        before=(capture.PROGRAM,live.PROGRAM,capture.check_build)
        self.assertNotIn(self.engine.PROGRAM,(capture.PROGRAM,live.PROGRAM))
        self.assertIs(self.engine.check_build,collect.check_build)
        self.assertEqual(before,(capture.PROGRAM,live.PROGRAM,capture.check_build))

    def test_smoke_build_cannot_be_used_as_stored_image_build(self):
        from tools.inference import SetupError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'manifest.json').write_text(json.dumps(dict(target='smoke')))
            with mock.patch.object(collect,'FIRMWARE',root),mock.patch.object(collect.accepted,'check_build') as check:
                with self.assertRaises(SetupError):collect.check_build(Path('unused'))
                check.assert_not_called()
