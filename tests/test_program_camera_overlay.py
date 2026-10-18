import json
from unittest import mock

from tools import inference
from tools import program_camera_overlay as overlay
from tools import program_camera_inference as capture
import test_program_stored_image as guards


class ProgramOverlayTests(guards.ProgramStoredImageTests):
    """Run all existing confirmation/TOCTOU/failure guards against the UI writer."""
    def setUp(self):
        self.engine = overlay.writer_engine()
        self.enter_context(mock.patch.object(guards, "program", self.engine))
        super().setUp()
        self.receipt.update(target=overlay.prepared.TARGET, binary_bytes=4726448)
        (self.stage / "receipt.json").write_text(json.dumps(self.receipt))

    def test_other_target_phrases_do_not_authorize_test_ui(self):
        for phrase in ("PROGRAM E8-HP CAMERA INFERENCE", "PROGRAM E8-HP STORED IMAGE", "PROGRAM E8-HP TEST UI extra"):
            self.confirm.return_value = phrase
            with self.subTest(phrase=phrase), self.assertRaises(inference.SetupError):
                self.engine.program(self.port, True)
        self.execute.assert_not_called()

    def test_entrypoint_routes_only_overlay_package_without_modifying_capture_writer(self):
        before = (capture.prepared, capture.CONFIRMATION, capture.print_review)
        with mock.patch.object(overlay, "writer_engine", return_value=self.engine), \
             mock.patch.object(capture.prepared, "load_prepared") as wrong:
            overlay.program(self.port)
            wrong.assert_not_called()
        self.assertEqual(before, (capture.prepared, capture.CONFIRMATION, capture.print_review))
        self.assertIn("TEST UI ONLY / NOT LIVE ASL", self.output.getvalue())
        self.assertIn(overlay.prepared.IMAGE_NAME, self.output.getvalue())
        self.execute.assert_not_called()

    def test_attempt_records_overlay_helper_identity(self):
        self.engine.program(self.port, True)
        record = json.loads(self.records()[0].read_text())
        self.assertEqual(record["writer_helper_sha256"], inference.sha256(overlay.__file__))
        self.assertEqual(record["target"], overlay.prepared.TARGET)

    def test_public_cli_requires_explicit_flags_and_review_default(self):
        for args in (("program",), ("review", "--write"), ("program", "--port", self.port, "--w")):
            with mock.patch.object(overlay.sys, "argv", ["program_camera_overlay", *args]):
                with self.assertRaises(SystemExit) as error:
                    overlay.main()
                self.assertEqual(error.exception.code, 2)
        work = self.stage / "public-cli"
        work.mkdir()
        with mock.patch.object(overlay.sys, "argv", ["program_camera_overlay", "program", "--port", self.port]), \
             mock.patch.object(overlay.common, "prepare_work"), mock.patch.object(overlay.common, "WORK", work), \
             mock.patch.object(overlay, "program") as run:
            self.assertEqual(overlay.main(), 0)
            run.assert_called_once_with(self.port, False)
        self.execute.assert_not_called()
