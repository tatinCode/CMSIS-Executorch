import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

from tools import inference
from tools import prepare_stored_image as prepare


def baseline():
    return {
        "DEVICE": {"disabled": False, "binary": "app-device-config.json", "version": "0.5.00", "signed": True},
        "USER_APP": {"binary": "alif-img.bin", "mramAddress": "0x80200000", "version": "1.0.0",
                     "cpu_id": "M55_HP", "flags": ["boot"], "signed": False},
    }


def package_fixture(image_bytes=1984):
    start = 0x8057F0D0
    device = bytes(336)
    table = b"OEMTOC01" + struct.pack("<II", 0x20020, 0x20020) + b"AE822FA0E5597LS0"
    table += struct.pack("<5I12s", 0x8057F440, len(device), 3, 0xF, 0x50000, b"DEVICE")
    table += struct.pack("<5I12s", start, image_bytes, 4, 0x42, 0x1000000, b"USER_APP")
    crc = zlib.crc32(table)
    table += struct.pack("<4I", crc, 0x8057FF90, start, 3888)
    package = bytes(880 + 1680 + 880) + device + table
    text = f"""* * * User managed MRAM locations* * *
0x80001000 0x{image_bytes:x} {image_bytes} unsigned {prepare.IMAGE_NAME}
* * * APP Package Start Address* * *
0x8057f0d0 0x370 880 SB{prepare.IMAGE_NAME}.crt
0x8057f440 0x690 1680 Key1/Key2 Certificates
0x8057fad0 0x370 880 SBapp-device-config.bin.crt
0x8057fe40 0x150 336 app-device-config.bin
0x8057ff90 0x20 32 APP TOC Header
0x8057ffb0 0x20 32 APP TOC entry for DEVICE\x00 obj_address 0x8057f440
0x8057ffd0 0x20 32 APP TOC entry for USER_APP obj_address 0x8057f0d0
0x8057fff0 0x10 16 APP TOC Tail
 - APP Package total size: 3888 bytes
 - APP Package Start Address: 0x8057f0d0
 - APP TOC size: 112 bytes
 - APP TOC Start Address: 0x8057ff90
 - APP CRC32: {hex(crc)}
"""
    return text, package, device


class PrepareStoredImageTests(unittest.TestCase):
    def test_only_opt_in_large_hp_image_and_baseline_device_are_selected(self):
        original = baseline()
        config = prepare.make_config(original)
        self.assertEqual(config["DEVICE"], original["DEVICE"])
        self.assertEqual(config["USER_APP"]["mramAddress"], "0x80001000")
        self.assertEqual(config["USER_APP"]["binary"], prepare.IMAGE_NAME)
        self.assertEqual(config["USER_APP"]["cpu_id"], "M55_HP")
        self.assertEqual(original, baseline())
        # Existing smoke validator still rejects the larger-profile config.
        with self.assertRaises(inference.SetupError):
            prepare.shared.make_toc_config(config)

    def test_baseline_extra_core_and_unexpected_load_address_fail(self):
        for field, value in (("cpu_id", "M55_HE"), ("loadAddress", "0x58000000"), ("flags", ["load", "boot"])):
            config = baseline()
            config["USER_APP"][field] = value
            with self.subTest(field=field), self.assertRaises(inference.SetupError):
                prepare.make_config(config)
        config = baseline()
        config["OTHER_CORE"] = {}
        with self.assertRaises(inference.SetupError):
            prepare.make_config(config)

    def test_complete_tail_and_actual_binary_hp_toc_are_checked(self):
        text, package, device = package_fixture()
        result = prepare.verify_package(text, package, 1984, device)
        self.assertEqual(result["image_end"], hex(0x80001000 + 1984))
        self.assertEqual(result["package_address"], "0x8057f0d0")
        self.assertEqual(result["package_end"], "0x80580000")
        self.assertTrue(result["binary_toc_verified"])
        self.assertEqual(result["boot_cpu"], "M55_HP")

    def test_wrong_image_address_name_size_and_extra_images_fail(self):
        text, package, device = package_fixture()
        changes = [("0x80001000", "0x80200000"), ("1984 unsigned", "1983 unsigned"),
                   (prepare.IMAGE_NAME, "handgpt-smoke.bin"),
                   ("* * * APP Package Start", "0x80000000 0x10 16 unsigned other.bin\n* * * APP Package Start")]
        for old, new in changes:
            with self.subTest(old=old), self.assertRaises(inference.SetupError):
                prepare.verify_package(text.replace(old, new), package, 1984, device)
        for size in (0, 1985, prepare.PROFILE.limits["mram_bytes"] + 16):
            with self.subTest(size=size), self.assertRaises(inference.SetupError):
                prepare.verify_package(text, package, size, device)

    def test_missing_unknown_overlapping_or_outside_tail_objects_fail(self):
        text, package, device = package_fixture()
        changes = [("0x8057f440 0x690", "0x8057f430 0x690"),
                   ("0x8057f0d0 0x370", "0x804ff000 0x370"),
                   ("Key1/Key2 Certificates", "unexpected image"),
                   ("for USER_APP", "for HE_APP"),
                   ("0x8057fad0 0x370 880 SBapp-device-config.bin.crt\n", ""),
                   (" - APP Package total size: 3888 bytes", " - APP Package total size: 3889 bytes"),
                   ("0x8057fff0 0x10", "0x8057fff0 0x20")]
        for old, new in changes:
            with self.subTest(old=old), self.assertRaises(inference.SetupError):
                prepare.verify_package(text.replace(old, new), package, 1984, device)
        with self.assertRaises(inference.SetupError):
            prepare.verify_package(text, bytes(0x80010), 1984, device)

    def test_binary_not_just_map_must_select_hp_and_match_tail_crc(self):
        text, package, device = package_fixture()
        # Header, device identity, CPU/flags, object address, tail and payload.
        for index in (len(package) - 112, len(package) - 90, len(package) - 48 + 8,
                      len(package) - 48 + 12, len(package) - 48, len(package) - 16,
                      len(package) - 4, 880 + 1680 + 880):
            altered = bytearray(package)
            altered[index] ^= 1
            with self.subTest(index=index), self.assertRaises(inference.SetupError):
                prepare.verify_package(text, altered, 1984, device)

    def test_duplicate_summary_is_rejected(self):
        text, package, device = package_fixture()
        with self.assertRaises(inference.SetupError):
            prepare.verify_package(text + " - APP Package Start Address: 0x8057f0d0\n", package, 1984, device)

    def test_preservation_confirmation_is_required_before_any_preparation(self):
        with mock.patch.object(prepare, "check_build") as check, mock.patch.object(prepare.shared, "run_logged") as execute:
            with self.assertRaises(inference.SetupError):
                prepare.prepare("/unused", Path("/unused"), False)
            check.assert_not_called()
            execute.assert_not_called()

    def test_missing_changed_and_extra_build_identities_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").write_text("a")
            recorded = {"a": inference.sha256(root / "a")}
            prepare.identities(recorded, {"a"}, root)
            for paths in (set(), {"a", "b"}):
                with self.assertRaises(inference.SetupError):
                    prepare.identities(recorded, paths, root)
            (root / "a").write_text("changed")
            with self.assertRaises(inference.SetupError):
                prepare.identities(recorded, {"a"}, root)
            with self.assertRaises(inference.SetupError):
                prepare.identities({"../escape": "anything"}, {"../escape"}, root)

    def test_smoke_build_cannot_be_used_as_stored_image_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"target": "DevKit-E8-HP", "build": "debug"}))
            with mock.patch.object(prepare, "FIRMWARE", root), self.assertRaises(inference.SetupError):
                prepare.check_build(Path("unused"))

    def test_prepare_review_are_isolated_and_detect_stale_or_tampered_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            for folder in ("build/config", "build/images", "build/logs", "utils", "cert"):
                (installed / folder).mkdir(parents=True, exist_ok=True)
            for name in ("app-gen-toc", "app-write-mram", "app-sign-image"):
                (installed / name).write_text("not executed in tests")
                (installed / name).chmod(0o700)
            (installed / "version.txt").write_text("1.112.00")
            settings = {"DEVICE": {"Part#": "E8 (AE822FA0E5597LS0)", "Revision": "A0"}, "MRAM-BURNER": {"Interface": "isp"}}
            (installed / "utils/global-cfg.db").write_text(json.dumps(settings))
            (installed / "build/config/app-device-config.json").write_text("{}")
            (installed / "build/images/app-device-config.bin").write_bytes(b"old generated image")
            (installed / "build/AppTocPackage.bin").write_bytes(b"old TOC")
            binary = root / "image.bin"
            binary.write_bytes(bytes(1984))
            info = {"binary": binary, "binary_sha256": inference.sha256(binary), "binary_bytes": 1984,
                    "build_manifest_sha256": "manifest identity", "image": str(root / "cat.jpg")}
            smoke_program = root / "smoke-programming"
            smoke_program.mkdir()
            (smoke_program / "latest.json").write_text("preserve smoke pointer")

            def generate(command, cwd, log):
                self.assertEqual(command, [cwd / "app-gen-toc", "-f", prepare.CONFIG_NAME])
                self.assertNotEqual(cwd, installed)
                self.assertFalse((cwd / "build/AppTocPackage.bin").exists())
                text, package, device = package_fixture()
                (cwd / "build/AppTocPackage.bin").write_bytes(package)
                (cwd / "build/app-package-map.txt").write_text(text)
                (cwd / "build/images/app-device-config.bin").write_bytes(device)

            with mock.patch.object(prepare, "PROGRAM", root / "stored-programming"), \
                 mock.patch.object(prepare.shared, "PROGRAM", smoke_program), \
                 mock.patch.object(prepare, "baseline_config", side_effect=baseline), \
                 mock.patch.object(prepare, "check_build", return_value=info), \
                 mock.patch.object(prepare.common, "check_space"), \
                 mock.patch.object(prepare.shared, "run_logged", side_effect=generate) as execute, \
                 contextlib.redirect_stdout(io.StringIO()):
                before = prepare.shared.protected_state(installed)
                prepare.prepare(installed, root / "cat.jpg", True)
                execute.assert_called_once()
                self.assertEqual(before, prepare.shared.protected_state(installed))
                self.assertEqual((smoke_program / "latest.json").read_text(), "preserve smoke pointer")
                receipt, stage = prepare.load_prepared()
                self.assertFalse(receipt["hardware_programmed"])
                self.assertFalse(receipt["programming_authorized"])
                self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
                toolkit = stage / "setools"
                for name in ("build/AppTocPackage.bin", "build/images/" + prepare.IMAGE_NAME,
                             "app-write-mram", prepare.CONFIG_NAME):
                    path = toolkit / name
                    saved = path.read_bytes()
                    path.write_bytes(saved + b"changed")
                    with self.subTest(name=name), self.assertRaises(inference.SetupError):
                        prepare.load_prepared()
                    path.write_bytes(saved)
                (toolkit / "build/images/unexpected.bin").write_bytes(b"extra")
                with self.assertRaises(inference.SetupError):
                    prepare.load_prepared()
                execute.assert_called_once() # Review never invokes any SETOOLS executable.

    def test_cli_has_no_program_command_or_write_flag(self):
        for args in (("program", "--write"), ("review", "--write")):
            result = subprocess.run([sys.executable, "-m", "tools.prepare_stored_image", *args],
                                    cwd=inference.ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
