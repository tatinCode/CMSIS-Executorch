import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools import demo_asl as demo, replay_asl
from tools.inference import SetupError
import test_prepare_camera_inference as packages
import test_program_stored_image as writers


class DemoTests(unittest.TestCase):
    def test_only_frozen_candidate_is_accepted_without_rewriting_failed_gates(self):
        value = dict(replay_trial=True,checkpoint_sha256=demo.CHECKPOINT,
                     deployment_approved=False,hardware_verified=False,protected_letters_int8_passed=False)
        self.assertIs(demo.validate_demo(value),value)
        with self.assertRaises(SetupError):
            replay_asl.validate_replay(value)
        with self.assertRaises(SetupError):
            demo.validate_demo({**value,'checkpoint_sha256':'different'})
        self.assertFalse(value['protected_letters_int8_passed'])
        self.assertNotEqual(demo.engine().PROGRAM,replay_asl.PROGRAM)

    def test_build_needs_explicit_accuracy_acknowledgement(self):
        with mock.patch('sys.argv',['demo','build']), self.assertRaises(SetupError):
            demo.main()


class DemoPackages(packages.PrepareCameraInferenceTests):
    def setUp(self):
        self.module = demo.engine()
        self.engine = self.module.package_engine()
        patch = mock.patch.object(packages,'prepare',self.engine)
        patch.__enter__();self.addCleanup(patch.__exit__,None,None,None)
        super().setUp()

    def test_smoke_build_cannot_be_used_as_stored_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'manifest.json').write_text(json.dumps(dict(target='smoke')))
            with mock.patch.object(self.module,'WORKSPACE',root), self.assertRaises(SetupError):
                self.module.check_build(Path('unused'))


class DemoWriters(writers.ProgramStoredImageTests):
    def setUp(self):
        self.engine = demo.engine().writer_engine()
        patch=mock.patch.object(writers,'program',self.engine)
        patch.__enter__();self.addCleanup(patch.__exit__,None,None,None)
        super().setUp()

    def test_replay_confirmation_cannot_authorize_best_effort_write(self):
        with mock.patch('builtins.input',return_value='PROGRAM E8-HP REPLAY ASL'),self.assertRaises(SetupError):
            self.engine.program(self.port,True)
        self.execute.assert_not_called()
