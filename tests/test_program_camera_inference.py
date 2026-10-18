import json
from unittest import mock

from tools import inference
from tools import program_camera_inference as program
from tools import program_stored_image as stored_program
import test_program_stored_image as guards


WRITER_TRANSPORT = program.run_writer
PORT_IDENTITY = program.port_identity


class ProgramCameraInferenceTests(guards.ProgramStoredImageTests):
    """Run the existing writer safety contract against the separate camera entry point."""

    def setUp(self):
        self.enter_context(mock.patch.object(guards, "program", program))
        super().setUp()
        self.receipt.update(target=program.prepared.TARGET, binary_bytes=4719744)
        self.receipt["package_layout"]["image_end"] = "0x80481480"
        (self.stage / "receipt.json").write_text(json.dumps(self.receipt))

    def test_other_target_confirmation_phrases_cannot_authorize_camera_write(self):
        for phrase in ("PROGRAM E8-HP STORED IMAGE", "PROGRAM E8-HP SMOKE", "PROGRAM E8-HP CAMERA INFERENCE extra"):
            self.confirm.return_value = phrase
            with self.subTest(phrase=phrase), self.assertRaises(inference.SetupError):
                program.program(self.port, True)
        self.execute.assert_not_called()

    def test_only_transport_utilities_are_reused_not_stored_image_package_selection(self):
        self.assertIs(WRITER_TRANSPORT, stored_program.run_writer)
        self.assertIs(program.validate_port, stored_program.validate_port)
        self.assertIs(PORT_IDENTITY, stored_program.port_identity)
        self.assertIsNot(program.prepared, stored_program.prepared)
        self.assertNotEqual(program.CONFIRMATION, stored_program.CONFIRMATION)
        with mock.patch.object(stored_program.prepared, "load_prepared") as wrong_target:
            program.program(self.port)
            wrong_target.assert_not_called()

    def test_review_identifies_coexistence_not_camera_pixel_classification(self):
        program.program(self.port)
        self.assertIn("camera-inference", self.output.getvalue())
        self.assertIn("NOT camera classification", self.output.getvalue())


class CameraPortIdentityTests(guards.PortIdentityTests):
    def setUp(self):
        context = mock.patch.object(guards, "program", program)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)


class CameraWriterTransportTests(guards.WriterTransportTests):
    def setUp(self):
        context = mock.patch.object(guards, "program", program)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
