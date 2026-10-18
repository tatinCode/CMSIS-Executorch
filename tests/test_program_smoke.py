import contextlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools import inference
from tools import program_smoke as program


class ProgramSmokeTests(unittest.TestCase):
    def package_map(self):
        return """* * * User managed MRAM locations* * *
0x80200000 0x100 256 unsigned handgpt-smoke.bin
* * * APP Package Start Address* * *
0x8057ffb0 0x20 32 APP TOC entry for DEVICE\x00\x00 obj_address 0x8057f440
0x8057ffd0 0x20 32 APP TOC entry for USER_APP obj_address 0x8057f0d0
 - APP Package total size: 3888 bytes
 - APP Package Start Address: 0x8057f0d0
"""

    def test_generated_map_confirms_application_and_toc_regions(self):
        layout = program.verify_package_map(self.package_map(), 256, 3888)
        self.assertEqual(layout["image_address"], "0x80200000")
        self.assertEqual(layout["package_address"], "0x8057f0d0")

    def test_wrong_image_core_region_size_or_extra_boot_entries_fail(self):
        for old, new in (("0x80200000", "0x80000000"), ("256 unsigned", "255 unsigned"),
                         ("handgpt-smoke.bin", "alif-img.bin"),
                         ("0x8057f0d0\n", "0x80580000\n"),
                         ("for USER_APP", "for OTHER_CORE")):
            with self.subTest(old=old):
                with self.assertRaises(inference.SetupError):
                    program.verify_package_map(self.package_map().replace(old, new), 256, 3888)

    def test_extra_user_managed_images_fail(self):
        text = self.package_map().replace("* * * APP Package Start", "0x80000000 0x10 16 unsigned other.bin\n* * * APP Package Start")
        with self.assertRaises(inference.SetupError):
            program.verify_package_map(text, 256, 3888)

    def config(self):
        return {
            "DEVICE": {"disabled": False, "binary": "app-device-config.json", "version": "0.5.00", "signed": True},
            "USER_APP": {"binary": "alif-img.bin", "mramAddress": "0x80200000", "version": "1.0.0", "cpu_id": "M55_HP", "flags": ["boot"], "signed": False},
        }

    def test_toc_preserves_device_and_changes_only_image(self):
        original = self.config()
        result = program.make_toc_config(original)
        self.assertEqual(result["DEVICE"], original["DEVICE"])
        self.assertEqual(result["USER_APP"]["binary"], program.IMAGE_NAME)
        self.assertEqual(original["USER_APP"]["binary"], "alif-img.bin")
        self.assertEqual(program.make_toc_config(result), result)

    def test_wrong_cpu_address_or_flags_are_rejected(self):
        for field, value in (("cpu_id", "M55_HE"), ("mramAddress", "0x80000000"),
                             ("flags", ["load", "boot"]), ("disabled", True)):
            config = self.config()
            config["USER_APP"][field] = value
            with self.assertRaises(inference.SetupError):
                program.make_toc_config(config)

    def test_extra_toc_images_are_not_silently_accepted(self):
        config = self.config()
        config["OTHER_CORE"] = {}
        with self.assertRaises(inference.SetupError):
            program.make_toc_config(config)

    def test_unexpected_load_address_is_not_silently_accepted(self):
        config = self.config()
        config["USER_APP"]["loadAddress"] = "0x58000000"
        with self.assertRaises(inference.SetupError):
            program.make_toc_config(config)

    def test_port_must_be_an_explicit_dev_path(self):
        program.validate_port("/dev/ttyACM0")
        program.validate_port("/dev/serial/by-id/usb-SEGGER_J-Link-if00")
        for value in ("", "auto", "COM3", "/dev/../etc/file", "/dev/ttyACM0\n"):
            with self.assertRaises(inference.SetupError):
                program.validate_port(value)

    def test_paths_cannot_escape_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(inference.SetupError):
                program.inside(Path(directory) / ".." / "other", directory)

    def test_program_is_review_only_without_write_flag(self):
        receipt = {"port": "/dev/ttyACM0"}
        with mock.patch.object(program, "load_prepared", return_value=(receipt, Path("/stage"), Path("/stage/setools"))), \
             mock.patch.object(program, "print_review"), mock.patch.object(program, "run_logged") as execute, \
             mock.patch("builtins.input") as confirm, contextlib.redirect_stdout(io.StringIO()):
            program.program(False)
            execute.assert_not_called()
            confirm.assert_not_called()

    def test_wrong_confirmation_never_runs_writer(self):
        receipt = {"port": "/dev/ttyACM0"}
        with mock.patch.object(program, "load_prepared", return_value=(receipt, Path("/stage"), Path("/stage/setools"))), \
             mock.patch.object(program, "print_review"), mock.patch.object(program, "run_logged") as execute, \
             mock.patch("builtins.input", return_value="yes"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(inference.SetupError):
                program.program(True)
            execute.assert_not_called()

    def test_port_change_during_confirmation_never_runs_writer(self):
        first = ({"port": "/dev/ttyACM0"}, Path("/stage"), Path("/stage/setools"))
        second = ({"port": "/dev/ttyACM1"}, Path("/stage"), Path("/stage/setools"))
        with mock.patch.object(program, "load_prepared", side_effect=[first, second]), \
             mock.patch.object(program, "print_review"), mock.patch.object(program, "run_logged") as execute, \
             mock.patch("builtins.input", return_value=program.CONFIRMATION), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(inference.SetupError):
                program.program(True)
            execute.assert_not_called()

    def test_confirmed_writer_uses_only_exact_port_and_padding_arguments(self):
        prepared = ({"port": "/dev/ttyACM0"}, Path("/stage"), Path("/stage/setools"))
        with mock.patch.object(program, "load_prepared", return_value=prepared), \
             mock.patch.object(program, "print_review"), mock.patch.object(program, "run_logged") as execute, \
             mock.patch("builtins.input", return_value=program.CONFIRMATION), \
             mock.patch.object(Path, "stat", return_value=mock.Mock(st_mode=stat.S_IFCHR)), \
             contextlib.redirect_stdout(io.StringIO()):
            program.program(True)
            execute.assert_called_once_with([Path("/stage/setools/app-write-mram"), "-p", "-c", "/dev/ttyACM0"],
                                            Path("/stage/setools"), Path("/stage/program.log"))

    def test_toolkit_checks_expected_device_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "utils").mkdir()
            for name in ("app-gen-toc", "app-write-mram", "app-sign-image"):
                (root / name).write_text("placeholder")
                (root / name).chmod(0o700)
            (root / "version.txt").write_text("1.112.00")
            settings = {"DEVICE": {"Part#": "E8 (AE822FA0E5597LS0)", "Revision": "A0"},
                        "MRAM-BURNER": {"Interface": "isp"}}
            (root / "utils/global-cfg.db").write_text(json.dumps(settings))
            self.assertEqual(program.validate_toolkit(root)[0], "1.112.00")
            settings["DEVICE"]["Part#"] = "E7"
            (root / "utils/global-cfg.db").write_text(json.dumps(settings))
            with self.assertRaises(inference.SetupError):
                program.validate_toolkit(root)

    def test_build_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / ".work/inference/firmware"
            binary = firmware / "out/smoke/DevKit-E8-HP/debug/smoke.bin"
            binary.parent.mkdir(parents=True)
            binary.write_bytes((0x20100000).to_bytes(4, "little") + (0x80201001).to_bytes(4, "little"))
            binary.with_suffix(".elf").write_bytes(b"test ELF")
            source = root / "firmware/smoke/src/main.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("// test source")
            config = firmware / "RTE/test.h"
            config.parent.mkdir()
            config.write_text("// test config")
            metadata = {
                "target": "DevKit-E8-HP", "build": "debug", "executorch_revision": inference.REVISION,
                "layout": {"vector_table_verified": True},
                "bin": str(binary.relative_to(root)), "bin_sha256": inference.sha256(binary),
                "elf": str(binary.with_suffix(".elf").relative_to(root)),
                "elf_sha256": inference.sha256(binary.with_suffix(".elf")),
                "source_sha256": {str(source.relative_to(root)): inference.sha256(source)},
                "configuration_sha256": {str(config.relative_to(firmware)): inference.sha256(config)},
                "generator_sha256": inference.sha256(Path(program.smoke.__file__)),
            }
            (firmware / "manifest.json").write_text(json.dumps(metadata))
            with mock.patch.object(program, "ROOT", root), mock.patch.object(program, "FIRMWARE", firmware):
                self.assertEqual(program.check_build()["binary_bytes"], 8)
                for path in (source, config, binary, binary.with_suffix(".elf")):
                    data = path.read_bytes()
                    path.write_bytes(data + b"changed")
                    with self.subTest(path=path):
                        with self.assertRaises(inference.SetupError):
                            program.check_build()
                    path.write_bytes(data)

    def test_prepare_and_review_use_private_toolkit_and_detect_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            installed.mkdir()
            for path in ("build/config", "build/images", "utils", "cert"):
                (installed / path).mkdir(parents=True, exist_ok=True)
            for name in ("app-gen-toc", "app-write-mram", "app-sign-image"):
                (installed / name).write_text("not executed by tests")
                (installed / name).chmod(0o700)
            (installed / "version.txt").write_text("1.112.00")
            settings = {"DEVICE": {"Part#": "E8 (AE822FA0E5597LS0)", "Revision": "A0"},
                        "MRAM-BURNER": {"Interface": "isp"}}
            (installed / "utils/global-cfg.db").write_text(json.dumps(settings))
            (installed / "build/config/app-device-config.json").write_text("{}")
            (installed / "build/images/app-device-config.bin").write_bytes(b"old device config")
            (installed / "build/AppTocPackage.bin").write_bytes(b"original TOC")
            vendor = root / "vendor"
            (vendor / ".alif").mkdir(parents=True)
            (vendor / ".alif/M55_HP_cfg.json").write_text(json.dumps(self.config()))
            binary = root / "smoke.bin"
            binary.write_bytes(bytes(256))
            info = {"binary": binary, "binary_sha256": inference.sha256(binary),
                    "build_manifest_sha256": "test manifest", "binary_bytes": 256}

            def generate(command, cwd, log):
                self.assertEqual(command, [cwd / "app-gen-toc", "-f", "handgpt-smoke.json"])
                self.assertNotEqual(cwd, installed)
                self.assertFalse((cwd / "build/AppTocPackage.bin").exists())
                (cwd / "build/AppTocPackage.bin").write_bytes(bytes(3888))
                (cwd / "build/app-package-map.txt").write_text(self.package_map())

            with mock.patch.object(program, "PROGRAM", root / "programming"), \
                 mock.patch.object(program.smoke, "VENDOR", vendor), \
                 mock.patch.object(program, "check_build", return_value=info), \
                 mock.patch.object(program.common, "check_space"), \
                 mock.patch.object(program, "run_logged", side_effect=generate) as execute, \
                 contextlib.redirect_stdout(io.StringIO()):
                before = program.protected_state(installed)
                program.prepare(str(installed), "/dev/ttyACM0")
                execute.assert_called_once()
                self.assertEqual(before, program.protected_state(installed))
                receipt, _, toolkit = program.load_prepared()
                self.assertEqual(receipt["port"], "/dev/ttyACM0")
                self.assertTrue(program.CRITICAL_FILES.issubset(receipt["files"]))
                (toolkit / "build/images/handgpt-smoke.bin").write_bytes(b"different")
                with self.assertRaises(inference.SetupError):
                    program.load_prepared()

    def test_root_tasks_have_no_automatic_programming(self):
        tasks = json.loads((program.ROOT / ".vscode/tasks.json").read_text())
        writers = [task for task in tasks["tasks"] if "--write" in task.get("args", [])]
        self.assertEqual(len(writers), 1)
        self.assertIn("WRITES BOARD", writers[0]["label"])
        self.assertNotIn("dependsOn", writers[0])
        for task in tasks["tasks"]:
            self.assertNotIn("runOn", task.get("runOptions", {}))


if __name__ == "__main__":
    unittest.main()
