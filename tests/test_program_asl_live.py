import json
from unittest import mock

from tools import inference
from tools import program_asl_live as live
from tools import program_camera_inference as capture
import test_program_stored_image as guards


class ProgramLiveTests(guards.ProgramStoredImageTests):
    def setUp(self):
        self.engine = live.writer_engine()
        self.enter_context(mock.patch.object(guards, "program", self.engine))
        super().setUp()
        self.receipt.update(target=live.prepared.TARGET)
        (self.stage / "receipt.json").write_text(json.dumps(self.receipt))

    def test_other_target_confirmations_cannot_authorize_experimental_live(self):
        for phrase in ("PROGRAM E8-HP TEST UI", "PROGRAM E8-HP CAMERA INFERENCE", "PROGRAM E8-HP STORED IMAGE"):
            self.confirm.return_value = phrase
            with self.subTest(phrase=phrase), self.assertRaises(inference.SetupError):
                self.engine.program(self.port, True)
        self.execute.assert_not_called()

    def test_writer_is_private_and_final_instructions_are_for_asl(self):
        before = (capture.prepared, capture.CONFIRMATION, capture.print_review)
        self.engine.program(self.port, True)
        self.assertEqual(before, (capture.prepared, capture.CONFIRMATION, capture.print_review))
        self.assertIn("ASL SELFTEST PASS", self.output.getvalue())
        self.assertNotIn("Require COEXIST SELFTEST PASS", self.output.getvalue())
        record = json.loads(self.records()[0].read_text())
        self.assertEqual(record["writer_helper_sha256"], inference.sha256(live.__file__))
