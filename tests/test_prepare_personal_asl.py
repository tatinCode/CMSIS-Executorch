import json
from pathlib import Path
import tempfile
from unittest import mock
from tools import prepare_personal_asl as personal, prepare_asl_live as baseline, inference
import test_prepare_camera_inference as guards


class PersonalPackageTests(guards.PrepareCameraInferenceTests):
    def setUp(self):
        self.engine=personal.package_engine()
        patch=mock.patch.object(guards,'prepare',self.engine)
        patch.__enter__();self.addCleanup(patch.__exit__,None,None,None)
        super().setUp()

    def test_personal_pointer_and_validator_are_separate(self):
        self.assertNotEqual(self.engine.PROGRAM,baseline.PROGRAM)
        self.assertIs(self.engine.check_build,personal.firmware.check_build)
        with mock.patch.object(personal,'package_engine') as engine:
            with self.assertRaises(inference.SetupError):personal.prepare('/unused',Path('/unused'),True,False)
            engine.assert_not_called()

    def test_smoke_build_cannot_be_used_as_stored_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'manifest.json').write_text(json.dumps(dict(target='smoke')))
            with mock.patch.object(personal.firmware,'WORKSPACE',root):
                with self.assertRaises(inference.SetupError):personal.firmware.check_build(Path('unused'))
