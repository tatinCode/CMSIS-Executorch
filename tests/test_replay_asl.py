import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import replay_asl as replay, personal_asl_firmware as baseline
from tools.inference import SetupError
import test_prepare_camera_inference as package_guards
import test_program_stored_image as writer_guards


class ReplayTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('torch'), 'Isolated torch environment required')
    def test_existing_export_manifest_is_finalized_atomically(self):
        from ml.asl.replay_export import write_final_manifest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.json'
            path.write_text('{"base":true}')
            write_final_manifest(path, dict(base=True, protected_letters_int8_passed=False))
            self.assertFalse(json.loads(path.read_text())['protected_letters_int8_passed'])
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ['manifest.json'])

    def test_acceptance_receipt_is_external_and_cannot_bind_another_manifest(self):
        from tools.inference import sha256
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'export'
            root.mkdir()
            manifest = root / 'manifest.json'
            manifest.write_text('{}')
            receipt = root / 'replay-acceptance.json'
            receipt.write_text(json.dumps(dict(manifest_sha256='bad')))
            with self.assertRaises(SetupError):
                replay.finalize_export(root)
            receipt.write_text(json.dumps(dict(manifest_sha256=sha256(manifest))))
            replay.finalize_export(root)
            self.assertFalse(receipt.exists())
            self.assertTrue(root.with_name('export-acceptance.json').exists())
            replay.finalize_export(root)

    def report(self):
        return dict(replay_trial=True, protected_letters_int8_passed=True,
                    protected_display_floors=dict(A=3, L=5, Y=3),
                    pt2e_quantized_validation=dict(negative_false_letter_count=0, wrong_letter_count=1,
                        per_class={c:dict(correct_display_count=n) for c,n in dict(A=3,L=5,Y=3).items()}))

    def test_individual_letters_cannot_be_hidden_by_aggregate_improvement(self):
        report = self.report()
        self.assertIs(replay.validate_replay(report), report)
        for label in ('A', 'L', 'Y'):
            bad = copy.deepcopy(report)
            bad['pt2e_quantized_validation']['per_class'][label]['correct_display_count'] -= 1
            with self.assertRaises(SetupError):
                replay.validate_replay(bad)
        for key, value in (('negative_false_letter_count',1),('wrong_letter_count',2)):
            bad = copy.deepcopy(report)
            bad['pt2e_quantized_validation'][key] = value
            with self.assertRaises(SetupError):
                replay.validate_replay(bad)
        for bad in ({}, {**report, 'replay_trial':False}, {**report, 'protected_letters_int8_passed':False}):
            with self.assertRaises(SetupError):
                replay.validate_replay(bad)

    def test_original_workspace_and_package_pointers_do_not_change(self):
        before = baseline.WORKSPACE
        engine = replay.builder()
        self.assertEqual(baseline.WORKSPACE, before)
        self.assertNotEqual(engine.WORKSPACE, before)
        self.assertIn(Path(replay.__file__), engine.source_paths())
        self.assertEqual(replay.package_engine().PROGRAM, replay.PROGRAM)
        self.assertIs(replay.package_engine().check_build, replay.check_build)

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'Isolated torch environment required')
    def test_float_selection_and_int8_packaging_use_same_letter_floors(self):
        from ml.asl.replay_train import FLOORS, protected_gate
        before = dict(correct_letter_count=12, wrong_letter_count=1, negative_false_letter_count=0)
        after = {**before, 'correct_letter_count':16,
                 'per_class':{c:dict(correct_display_count=n) for c,n in FLOORS.items()}}
        self.assertTrue(protected_gate(before, after))
        for c in FLOORS:
            bad = copy.deepcopy(after)
            bad['per_class'][c]['correct_display_count'] -= 1
            self.assertFalse(protected_gate(before, bad))


class ReplayPackageTests(package_guards.PrepareCameraInferenceTests):
    def setUp(self):
        self.engine = replay.package_engine()
        patch = mock.patch.object(package_guards, 'prepare', self.engine)
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        super().setUp()

    def test_smoke_build_cannot_be_used_as_stored_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'manifest.json').write_text(json.dumps(dict(target='smoke')))
            with mock.patch.object(replay, 'WORKSPACE', root), self.assertRaises(SetupError):
                replay.check_build(Path('unused'))


class ReplayWriterTests(writer_guards.ProgramStoredImageTests):
    def setUp(self):
        self.engine = replay.writer_engine()
        patch = mock.patch.object(writer_guards, 'program', self.engine)
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        super().setUp()

    def test_previous_personal_phrase_cannot_authorize_replay(self):
        with mock.patch('builtins.input', return_value='PROGRAM E8-HP PERSONAL ASL'), self.assertRaises(SetupError):
            self.engine.program(self.port, True)
        self.execute.assert_not_called()
