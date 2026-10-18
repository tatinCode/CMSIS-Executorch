from unittest import mock
from tools import program_personal_asl as personal
from tools.inference import SetupError
import test_program_stored_image as guards


class PersonalWriterTests(guards.ProgramStoredImageTests):
    def setUp(self):
        self.engine=personal.writer_engine()
        patch=mock.patch.object(guards,'program',self.engine)
        patch.__enter__();self.addCleanup(patch.__exit__,None,None,None)
        super().setUp()

    def test_collector_and_old_live_confirmation_cannot_authorize_personal_write(self):
        for phrase in ('PROGRAM E8-HP CONTINUOUS COLLECTOR','PROGRAM E8-HP EXPERIMENTAL LIVE ASL'):
            with mock.patch('builtins.input',return_value=phrase),self.assertRaises(SetupError):
                self.engine.program(self.port,True)
        self.execute.assert_not_called()
