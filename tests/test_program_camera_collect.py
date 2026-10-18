from unittest import mock
from tools import program_camera_collect as collect
import test_program_stored_image as guards


class CollectWriterTests(guards.ProgramStoredImageTests):
    def setUp(self):
        self.engine=collect.writer_engine()
        patch=mock.patch.object(guards,'program',self.engine)
        patch.__enter__();self.addCleanup(patch.__exit__,None,None,None)
        super().setUp()

    def test_live_and_capture_confirmations_cannot_authorize_collection(self):
        from tools.inference import SetupError
        for text in ('PROGRAM E8-HP EXPERIMENTAL LIVE ASL','PROGRAM E8-HP CAMERA INFERENCE','PROGRAM E8-HP TEST UI'):
            with mock.patch('builtins.input',return_value=text),self.assertRaises(SetupError):
                self.engine.program(self.port,True)
        self.execute.assert_not_called()
