import contextlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools import inference
from tools import program_stored_image as program


class ProgramStoredImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stage = Path(self.directory.name)
        self.receipt = {
            "target": "stored-image.debug+DevKit-E8-HP", "binary_bytes": 4653520, "binary_sha256": "a" * 64,
            "package_layout": {"image_address": "0x80001000", "image_end": "0x804711d0",
                               "package_address": "0x8057f0d0", "package_end": "0x80580000", "package_bytes": 3888},
        }
        (self.stage / "receipt.json").write_text(json.dumps(self.receipt))
        self.port = "/dev/ttyACM0"
        self.identity = {"resolved": self.port, "device": 1, "inode": 2, "rdev": 3}
        self.output = io.StringIO()
        self.enter_context(contextlib.redirect_stdout(self.output))
        self.enter_context(contextlib.redirect_stderr(self.output))
        self.load = self.enter_context(mock.patch.object(program.prepared, "load_prepared", return_value=(self.receipt, self.stage)))
        self.execute = self.enter_context(mock.patch.object(program, "run_writer"))
        self.confirm = self.enter_context(mock.patch("builtins.input", return_value=program.CONFIRMATION))
        self.terminal = self.enter_context(mock.patch.object(program.sys.stdin, "isatty", return_value=True))
        self.port_stat = self.enter_context(mock.patch.object(program, "port_identity", return_value=self.identity))

    # addCleanup/enterContext are named differently on the oldest host Python
    # supported by the isolated environment (3.10).
    def enter_context(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def records(self):
        return list(self.stage.glob("program-*/attempt.json"))

    def test_default_is_review_only_no_port_inspection_prompt_writer_or_attempt(self):
        program.program(self.port)
        self.execute.assert_not_called()
        self.confirm.assert_not_called()
        self.port_stat.assert_not_called()
        self.assertEqual(self.records(), [])
        self.assertIn("REVIEW ONLY", self.output.getvalue())

    def test_write_requires_interactive_terminal_and_rejects_piped_confirmation(self):
        self.terminal.return_value = False
        with self.assertRaisesRegex(inference.SetupError, "interactive terminal"):
            program.program(self.port, True)
        self.confirm.assert_not_called()
        self.port_stat.assert_not_called()
        self.execute.assert_not_called()

    def test_wrong_confirmation_never_runs_writer(self):
        for phrase in ("yes", "PROGRAM E8-HP SMOKE", "PROGRAM E8-HP STORED IMAGE extra", ""):
            self.confirm.return_value = phrase
            with self.subTest(phrase=phrase), self.assertRaisesRegex(inference.SetupError, "did not match"):
                program.program(self.port, True)
        self.execute.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_eof_or_cancelled_confirmation_never_runs_writer(self):
        for error in (EOFError(), KeyboardInterrupt()):
            self.confirm.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                program.program(self.port, True)
        self.execute.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_initial_stale_or_invalid_package_fails_before_confirmation(self):
        self.load.side_effect = inference.SetupError("Prepared package/tool identities changed")
        with self.assertRaises(inference.SetupError):
            program.program(self.port, True)
        self.confirm.assert_not_called()
        self.execute.assert_not_called()

    def test_post_confirmation_build_or_tool_verification_failure_blocks_writer(self):
        self.load.side_effect = [(self.receipt, self.stage), inference.SetupError("Build/package changed")]
        with self.assertRaises(inference.SetupError):
            program.program(self.port, True)
        self.assertEqual(self.load.call_count, 2)
        self.execute.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_changed_receipt_content_or_selected_stage_blocks_writer(self):
        for selected in (({**self.receipt, "binary_sha256": "b" * 64}, self.stage),
                         (self.receipt, self.stage / "different")):
            self.load.side_effect = [(self.receipt, self.stage), selected]
            with self.subTest(selected=selected), self.assertRaisesRegex(inference.SetupError, "package changed"):
                program.program(self.port, True)
        self.execute.assert_not_called()

    def test_changed_receipt_file_during_confirmation_blocks_writer(self):
        def confirm(_):
            (self.stage / "receipt.json").write_text("changed receipt")
            return program.CONFIRMATION
        self.confirm.side_effect = confirm
        with self.assertRaisesRegex(inference.SetupError, "package changed"):
            program.program(self.port, True)
        self.execute.assert_not_called()

    def test_in_place_receipt_change_cannot_mutate_the_approved_snapshot(self):
        def confirm(_):
            self.receipt["binary_sha256"] = "changed"
            return program.CONFIRMATION
        self.confirm.side_effect = confirm
        with self.assertRaisesRegex(inference.SetupError, "package changed"):
            program.program(self.port, True)
        self.execute.assert_not_called()

    def test_port_disappearance_or_identity_change_blocks_writer(self):
        for changed in (OSError("unplugged"), {**self.identity, "resolved": "/dev/ttyACM1"},
                        {**self.identity, "inode": 99}, {**self.identity, "rdev": 99}):
            self.port_stat.side_effect = [self.identity, changed]
            with self.subTest(changed=changed), self.assertRaises((OSError, inference.SetupError)):
                program.program(self.port, True)
        self.execute.assert_not_called()

    def test_missing_or_noncharacter_port_blocks_before_confirmation(self):
        self.port_stat.side_effect = inference.SetupError("not a character device")
        with self.assertRaises(inference.SetupError):
            program.program(self.port, True)
        self.confirm.assert_not_called()
        self.execute.assert_not_called()

    def test_confirmed_writer_uses_only_reviewed_private_toolkit_exact_port_and_arguments(self):
        before = (self.stage / "receipt.json").read_bytes()
        program.program(self.port, True)
        self.execute.assert_called_once()
        command, cwd, log = self.execute.call_args.args
        self.assertEqual(command, [self.stage / "setools/app-write-mram", "-p", "-c", self.port])
        self.assertEqual(cwd, self.stage / "setools")
        self.assertEqual(log.name, "program.log")
        self.assertEqual(log.parent.parent, self.stage)
        self.assertEqual(log.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.load.call_count, 2)
        self.assertEqual(self.port_stat.call_count, 2)
        self.assertEqual((self.stage / "receipt.json").read_bytes(), before)
        record = json.loads(self.records()[0].read_text())
        self.assertEqual(record["status"], "tool_exited_successfully")
        self.assertEqual(record["binary_sha256"], self.receipt["binary_sha256"])
        self.assertFalse(record["hardware_verified"])
        self.assertIn("NOT an inference result", self.output.getvalue())

    def test_each_explicit_attempt_has_a_separate_log_and_does_not_change_receipt(self):
        before = (self.stage / "receipt.json").read_bytes()
        program.program(self.port, True)
        program.program(self.port, True)
        self.assertEqual(len(self.records()), 2)
        logs = [call.args[2] for call in self.execute.call_args_list]
        self.assertNotEqual(logs[0], logs[1])
        self.assertEqual((self.stage / "receipt.json").read_bytes(), before)

    def test_failed_or_interrupted_write_records_no_success_and_never_retries(self):
        for error, status in ((inference.SetupError("tool failure"), "writer_failed"),
                              (OSError("cannot launch"), "writer_failed"), (KeyboardInterrupt(), "interrupted")):
            self.execute.reset_mock()
            self.execute.side_effect = error
            before = set(self.records())
            with self.subTest(status=status), self.assertRaises(type(error)):
                program.program(self.port, True)
            self.execute.assert_called_once()
            record = json.loads((set(self.records()) - before).pop().read_text())
            self.assertEqual(record["status"], status)
            self.assertFalse(record["hardware_verified"])
        self.assertIn("MRAM may be partially written", self.output.getvalue())

    def test_audit_record_failure_prevents_launch(self):
        with mock.patch.object(program.reports, "write_report", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                program.program(self.port, True)
        self.execute.assert_not_called()

    def test_explicit_usb_port_syntax_only_no_auto_or_nonserial_devices(self):
        for value in ("/dev/ttyACM0", "/dev/ttyUSB12", "/dev/serial/by-id/usb-SEGGER-if00", "/dev/serial/by-path/pci-usb-0"):
            program.validate_port(value)
        for value in ("auto", "", "COM3", "/dev/null", "/dev/zero", "/dev/tty", "/dev/ttyACM0\n",
                      "/dev/../ttyACM0", "/dev/ttyACM0 --erase", "/dev/serial/by-id/../ttyACM0"):
            with self.subTest(value=value), self.assertRaises(inference.SetupError):
                program.program(value, True)
        self.load.assert_not_called()
        self.execute.assert_not_called()

    def test_cli_program_requires_explicit_port_and_write_is_not_default(self):
        for args in (("program",), ("program", "--write"), ("review", "--write"),
                     ("program", "--port", self.port, "--erase"), ("program", "--port", self.port, "--w")):
            with mock.patch.object(program.sys, "argv", ["program_stored_image", *args]):
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    program.main()
                self.assertEqual(error.exception.code, 2)
        work = self.stage / "work"
        work.mkdir()
        with mock.patch.object(program.sys, "argv", ["program_stored_image", "program", "--port", self.port]), \
             mock.patch.object(program.common, "prepare_work"), mock.patch.object(program.common, "WORK", work), \
             mock.patch.object(program, "program") as run:
            self.assertEqual(program.main(), 0)
            run.assert_called_once_with(self.port, False)
        self.execute.assert_not_called()

    def test_cli_lock_prevents_another_programming_command(self):
        work = self.stage / "work"
        work.mkdir()
        with mock.patch.object(program.sys, "argv", ["program_stored_image", "program", "--port", self.port, "--write"]), \
             mock.patch.object(program.common, "prepare_work"), mock.patch.object(program.common, "WORK", work), \
             mock.patch.object(program.fcntl, "flock", side_effect=BlockingIOError("locked")):
            self.assertEqual(program.main(), 1)
        self.load.assert_not_called()
        self.confirm.assert_not_called()
        self.execute.assert_not_called()

    def test_cli_reports_cancellation_without_retry(self):
        work = self.stage / "work"
        work.mkdir()
        with mock.patch.object(program.sys, "argv", ["program_stored_image", "program", "--port", self.port, "--write"]), \
             mock.patch.object(program.common, "prepare_work"), mock.patch.object(program.common, "WORK", work), \
             mock.patch.object(program, "program", side_effect=KeyboardInterrupt()):
            self.assertEqual(program.main(), 130)
        self.execute.assert_not_called()


class PortIdentityTests(unittest.TestCase):
    def test_tty_identity_uses_metadata_without_opening_device(self):
        metadata = mock.Mock(st_mode=stat.S_IFCHR, st_dev=1, st_ino=2, st_rdev=3)
        with mock.patch.object(Path, "resolve", return_value=Path("/dev/ttyACM0")), \
             mock.patch.object(Path, "stat", return_value=metadata), mock.patch("builtins.open") as opening:
            identity = program.port_identity("/dev/serial/by-id/usb-board")
            self.assertEqual(identity, {"resolved": "/dev/ttyACM0", "device": 1, "inode": 2, "rdev": 3})
            opening.assert_not_called()

    def test_alias_cannot_select_arbitrary_character_device_or_regular_file(self):
        with mock.patch.object(Path, "resolve", return_value=Path("/dev/null")), \
             self.assertRaises(inference.SetupError):
            program.port_identity("/dev/serial/by-id/not-a-serial-port")
        with mock.patch.object(Path, "resolve", return_value=Path("/dev/ttyACM0")), \
             mock.patch.object(Path, "stat", return_value=mock.Mock(st_mode=stat.S_IFREG)), \
             self.assertRaises(inference.SetupError):
            program.port_identity("/dev/ttyACM0")


class WriterTransportTests(unittest.TestCase):
    def test_child_has_no_stdin_or_shell_and_output_is_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [root / "app-write-mram", "-p", "-c", "/dev/ttyACM0"]
            process = mock.Mock(stdout=io.BytesIO(b"write output\n"))
            process.wait.return_value = 0
            with mock.patch.object(program.subprocess, "Popen") as spawn, contextlib.redirect_stdout(io.StringIO()):
                spawn.return_value.__enter__.return_value = process
                program.run_writer(command, root, root / "program.log")
                spawn.assert_called_once_with(list(map(str, command)), cwd=root, stdin=program.subprocess.DEVNULL,
                                              stdout=program.subprocess.PIPE, stderr=program.subprocess.STDOUT)
            self.assertEqual((root / "program.log").read_bytes(), b"write output\n")

    def test_nonzero_exit_or_zero_exit_with_tool_error_fails(self):
        for exit_code, output in ((1, b"failed"), (0, b"[ERROR] failed"),
                                  (0, b"Traceback (most recent call last):\nEOFError: no prompt input")):
            with self.subTest(exit_code=exit_code, output=output), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                process = mock.Mock(stdout=io.BytesIO(output))
                process.wait.return_value = exit_code
                with mock.patch.object(program.subprocess, "Popen") as spawn, contextlib.redirect_stdout(io.StringIO()):
                    spawn.return_value.__enter__.return_value = process
                    with self.assertRaises(inference.SetupError):
                        program.run_writer([root / "app-write-mram", "-p", "-c", "/dev/ttyACM0"], root, root / "program.log")
                    spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
