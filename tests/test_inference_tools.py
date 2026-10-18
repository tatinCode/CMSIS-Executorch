import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inference = load_tool("inference")
image_tool = load_tool("prepare_reference_image")
pte_tool = load_tool("inspect_reference_pte")


class InferenceToolsTests(unittest.TestCase):
    def methods(self):
        tensor = {"shape": [1, 16], "dtype": "FLOAT", "dim_order": [0, 1]}
        return [{"name": "forward", "inputs": [tensor.copy()], "outputs": [tensor.copy()],
                 "delegates": ["EthosUBackend"], "planned_buffer_bytes": [128]}]

    def test_reference_contract_and_delegation(self):
        pte_tool.validate_methods(self.methods(), "smoke")

    def test_cpu_only_model_is_rejected(self):
        methods = self.methods()
        methods[0]["delegates"] = []
        with self.assertRaises(ValueError):
            pte_tool.validate_methods(methods, "smoke")

    def test_wrong_input_layout_is_rejected(self):
        methods = self.methods()
        methods[0]["inputs"][0]["dim_order"] = [1, 0]
        with self.assertRaises(ValueError):
            pte_tool.validate_methods(methods, "smoke")

    def test_wrong_model_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            pte_tool.validate_methods(self.methods(), "mv2")

    def test_negative_memory_size_is_rejected(self):
        methods = self.methods()
        methods[0]["planned_buffer_bytes"] = [-1]
        with self.assertRaises(ValueError):
            pte_tool.validate_methods(methods, "smoke")

    def test_environment_is_isolated_and_parallelism_bounded(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/unrelated-learning-env", "CMAKE_ARGS": "-DBAD=ON", "CC": "unrelated-cross-gcc", "CFLAGS": "-DBAD"}):
            env = inference.build_environment(1)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("CMAKE_ARGS", env)
        self.assertNotIn("CC", env)
        self.assertNotIn("CFLAGS", env)
        self.assertEqual(env["VIRTUAL_ENV"], str(inference.VENV))
        self.assertEqual(env["CMAKE_BUILD_PARALLEL_LEVEL"], "1")
        self.assertEqual(env["PIP_REQUIRE_VIRTUALENV"], "true")
        self.assertEqual(env["TMPDIR"], str(inference.WORK / "tmp"))

    def test_jobs_must_be_positive(self):
        self.assertEqual(inference.positive_jobs("2"), 2)
        with self.assertRaises(argparse.ArgumentTypeError):
            inference.positive_jobs("0")

    def test_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data"
            path.write_bytes(b"abc")
            self.assertEqual(inference.sha256(path), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_missing_source_fails_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(inference, "SOURCE", Path(directory)), mock.patch.object(inference, "run") as run:
                with self.assertRaises(inference.SetupError):
                    inference.require_source()
                run.assert_not_called()

    def test_wrong_source_revision_is_not_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / ".git").mkdir()
            with mock.patch.object(inference, "SOURCE", path), mock.patch.object(inference, "run", return_value="wrong") as run:
                with self.assertRaises(inference.SetupError):
                    inference.require_source()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ["git", "rev-parse", "HEAD"])

    def test_low_space_fails(self):
        with mock.patch.object(inference.shutil, "disk_usage", return_value=mock.Mock(free=1024)):
            with self.assertRaises(inference.SetupError):
                inference.check_space(5)

    def test_gcc16_workaround_is_specific_to_host_warning(self):
        env = {}
        with mock.patch.object(inference, "run", side_effect=["Free Software Foundation", "16.2.1"]):
            inference.host_compiler_compatibility(env)
        self.assertEqual(env["CFLAGS"], "-Wno-error=unterminated-string-initialization")

    def test_older_gcc_does_not_get_gcc16_flag(self):
        env = {}
        with mock.patch.object(inference, "run", side_effect=["Free Software Foundation", "13.3.1"]):
            inference.host_compiler_compatibility(env)
        self.assertNotIn("CFLAGS", env)

    def test_missing_toolchain_fails_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(inference.SetupError):
                inference.find_toolchain(directory)

    def test_cmsis_requires_real_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(inference.SetupError):
                inference.find_cmsis(directory)
            include = Path(directory) / "CMSIS" / "Core" / "Include"
            include.mkdir(parents=True)
            (include / "cmsis_compiler.h").touch()
            self.assertEqual(inference.find_cmsis(directory), Path(directory))

    def test_arm_archive_check_accepts_only_arm_members(self):
        with mock.patch.object(inference, "run", return_value="  Machine: ARM\n  Machine: ARM\n"):
            inference.verify_arm_archive(Path("runtime.a"), Path("/compiler"))

    def test_host_archive_is_rejected(self):
        with mock.patch.object(inference, "run", return_value="  Machine: Advanced Micro Devices X86-64\n"):
            with self.assertRaises(inference.SetupError):
                inference.verify_arm_archive(Path("runtime.a"), Path("/compiler"))

    def test_empty_archive_is_rejected(self):
        with mock.patch.object(inference, "run", return_value=""):
            with self.assertRaises(inference.SetupError):
                inference.verify_arm_archive(Path("runtime.a"), Path("/compiler"))

    def test_mv2_requires_an_explicit_image_before_writing(self):
        args = argparse.Namespace(model="mv2", image=None, jobs=1)
        with mock.patch.object(inference, "require_source"), mock.patch.object(inference, "require_python"), mock.patch.object(inference, "prepare_work") as write:
            with self.assertRaises(inference.SetupError):
                inference.export_model(args)
            write.assert_not_called()

    def test_float_literals_are_valid_cpp_and_keep_fractions(self):
        self.assertEqual(image_tool.float_literal(0.0), "0.0f")
        self.assertEqual(image_tool.float_literal(-1.0), "-1.0f")
        self.assertEqual(image_tool.float_literal(0.125), "0.125f")
        self.assertIn("e", image_tool.float_literal(1e-20))

    def test_float_header_is_not_legacy_int8(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "input.h"
            image_tool.write_float_header([0.125, -1.0], output)
            text = output.read_text()
        self.assertIn("alignas(16) static const float reference_input[]", text)
        self.assertIn("0.125f, -1.0f", text)
        self.assertIn("reference_input_numel = 2", text)

    def test_model_header_keeps_every_byte_and_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "model.pte"
            header = Path(directory) / "model.h"
            source.write_bytes(bytes([0, 127, 128, 255]))
            inference.emit_model_header(source, header)
            text = header.read_text()
        self.assertIn("alignas(16) static const uint8_t", text)
        self.assertIn("0x00, 0x7f, 0x80, 0xff", text)
        self.assertIn("reference_model_pte_len = 4", text)

    def test_empty_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "empty.pte"
            source.touch()
            with self.assertRaises(inference.SetupError):
                inference.emit_model_header(source, Path(directory) / "model.h")

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler not available")
    def test_generated_headers_compile_as_cpp17(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.pte"
            model.write_bytes(bytes(range(32)))
            inference.emit_model_header(model, root / "model.h")
            image_tool.write_float_header([0.125, -1.0, 1e-20], root / "image.h")
            source = '#include "model.h"\n#include "image.h"\nstatic_assert(reference_input_numel == 3);\nstatic_assert(reference_model_pte_len == 32);\n'
            subprocess.run(["c++", "-std=c++17", "-Werror", "-x", "c++", "-fsyntax-only", "-I", directory, "-"],
                           input=source, text=True, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
