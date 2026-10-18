import copy
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools.asl_contract import STATIC_CLASSES, STATIC_LETTERS, DATASET_CLASSES, LEGACY_CLASSES, class_header, validate_classes
from tools import asl_selftest, inspect_asl_pte as inspector, observe_asl_live as observe
from tools.inference import ROOT

try:
    import torch
    from ml.asl import static_data, static_train, static_export
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


class StaticContractTests(unittest.TestCase):
    def test_exact_supported_set_and_generated_header(self):
        self.assertEqual(len(STATIC_LETTERS), 24)
        self.assertEqual(set(STATIC_LETTERS), set('ABCDEFGHIJKLMNOPQRSTUVWXYZ') - set('JZ'))
        self.assertEqual(STATIC_CLASSES[-2:], ('NO_HAND', 'OTHER'))
        self.assertEqual((ROOT/'firmware/asl_live/include/asl_classes.h').read_text(), class_header(STATIC_CLASSES))
        for bad in (list(reversed(STATIC_CLASSES)), list(STATIC_CLASSES[:-1]), ['A']*26, None,
                    (*STATIC_CLASSES[:-2], 'space', 'OTHER')):
            with self.assertRaises(ValueError): validate_classes(bad)

    def test_python_decisions_cover_all_letters_and_both_negatives(self):
        for i, label in enumerate(STATIC_CLASSES):
            values = [-20.]*26
            values[i] = 20.
            self.assertEqual(asl_selftest.displayed_decision(values, STATIC_CLASSES), label if i<24 else '--')
        self.assertEqual(asl_selftest.displayed_decision([0.]*26, STATIC_CLASSES), '--')
        self.assertFalse(asl_selftest.compare([1.]*7, [1.]*7, asl_selftest.policy(.125), classes=STATIC_CLASSES))

    def fixture(self):
        gate = asl_selftest.policy(.125)
        values = [-40.]*26
        values[12] = 20. # N; catches any accidental legacy index mapping.
        reference = dict(classes=list(STATIC_CLASSES), input_sha256='input', host_logits=values, selftest_gate=gate)
        manifest = dict(classes=list(STATIC_CLASSES), pte_sha256='model', selftest_gate=gate)
        log = ('HandGPT LIVE ASL EXPERIMENTAL; classes='+','.join(STATIC_CLASSES)+'; threshold=0.80\n'
               'model_sha256=model input_sha256=input\n'
               'ASL GATE revision=2 output_scale=0.125000000000 max_output_steps=8 min_cosine=0.999 threshold=0.80 post_hoc=1\n').encode()
        for repeat in (1,2,3):
            log += f'ASL SELFTEST repeat={repeat} max_error=0.000000 cosine=1.000000 method_high_water=864 temporary_high_water=491520 output_steps=0.000000\n'.encode()
            for i,v in enumerate(values): log += f'ASL LOGIT {i} actual={v} host={v}\n'.encode()
        log += b'ASL SELFTEST PASS checked=26 repeats=3\nLIVE ASL START;\n'
        log += b'LIVE ASL frames=10 predictions=7 display=N latency_ms=40 fault=0 IRQs=10 jobs=10/10\n'
        log += b'LIVE ASL frames=17 predictions=14 display=G latency_ms=40 fault=0 IRQs=17 jobs=17/17\n'
        return log, manifest, reference

    def test_observer_26_outputs_and_wrong_order_missing_logits_fail_closed(self):
        log,manifest,reference = self.fixture()
        self.assertTrue(observe.assess(log,manifest,reference)['boot_and_progress_checks_passed'])
        for bad in (log.replace(b'checked=26',b'checked=7'),log.replace(b'LOGIT 25',b'LOGIT 24'),
                    log.replace(b'display=N',b'display=J'), log.replace(b'display=G',b'display=Z'),
                    log.replace(b'classes=A,B,C,D',b'classes=A,B,D,C')):
            self.assertFalse(observe.assess(bad,manifest,reference)['boot_and_progress_checks_passed'])
        self.assertFalse(observe.assess(log,{**manifest,'classes':list(reversed(STATIC_CLASSES))},reference)['boot_and_progress_checks_passed'])
        self.assertFalse(observe.assess(log,manifest,{**reference,'classes':list(LEGACY_CLASSES)})['boot_and_progress_checks_passed'])

    def test_inspector_requires_exact_selected_output_count(self):
        from test_asl_export import method_fixture
        method = method_fixture()
        method['outputs'][0]['shape'] = [1,26]
        inspector.validate_methods([method], STATIC_CLASSES)
        with self.assertRaises(ValueError): inspector.validate_methods([method])
        for count in (7,25,27,1000):
            bad = copy.deepcopy(method); bad['outputs'][0]['shape'] = [1,count]
            with self.assertRaises(ValueError): inspector.validate_methods([bad], STATIC_CLASSES)

    def test_logged_output_steps_allow_only_scale_derived_uart_rounding(self):
        log, manifest, reference = self.fixture()
        gate = asl_selftest.policy(0.01)
        reference['host_logits'][0] = -0.04
        reference['selftest_gate'] = manifest['selftest_gate'] = gate
        log = log.replace(b'output_scale=0.125000000000', f'output_scale={gate["output_scale"]:.12f}'.encode())
        log = log.replace(b'ASL LOGIT 0 actual=-40.0 host=-40.0', b'ASL LOGIT 0 actual=-0.039999 host=-0.040000')
        log = log.replace(b'max_error=0.000000', b'max_error=0.000001')
        # An unrounded error 0.0000006 is 0.000060 steps, but rounded UART
        # actual logits reconstruct 0.000100 steps at this small output scale.
        log = log.replace(b'output_steps=0.000000', b'output_steps=0.000060')
        report = observe.assess(log, manifest, reference)
        self.assertTrue(report['boot_and_progress_checks_passed'], report['problems'])
        bad = log.replace(b'output_steps=0.000060', b'output_steps=0.010000')
        self.assertFalse(observe.assess(bad, manifest, reference)['boot_and_progress_checks_passed'])


@unittest.skipUnless(HAS_TORCH, 'Isolated torch environment required')
class StaticTrainingTests(unittest.TestCase):
    def test_static_imports_do_not_mutate_legacy_capture_training_contracts(self):
        from ml.asl import data, camera_data, model
        self.assertEqual(data.CLASSES, LEGACY_CLASSES[:-1])
        self.assertEqual(camera_data.CAMERA_CLASSES, LEGACY_CLASSES)
        self.assertEqual(model.ADAPTED_CLASSES, LEGACY_CLASSES)

    def test_expansion_copies_rows_by_name_not_index(self):
        model = torch.nn.Module()
        model.last_channel=4
        model.classifier=torch.nn.Sequential(torch.nn.Dropout(.2),torch.nn.Linear(4,7))
        before=copy.deepcopy(model.classifier[1].state_dict())
        static_train.expand_head(model,LEGACY_CLASSES)
        for i,label in enumerate(LEGACY_CLASSES):
            self.assertTrue(torch.equal(before['weight'][i], model.classifier[1].weight[STATIC_CLASSES.index(label)]))
            self.assertTrue(torch.equal(before['bias'][i], model.classifier[1].bias[STATIC_CLASSES.index(label)]))

    def test_manifests_exclude_commands_motion_and_cross_split_groups(self):
        rows=[]
        for split in ('train','val','test'):
            for label in DATASET_CLASSES:
                folder='nothing' if label=='NO_HAND' else label
                rows.append(dict(path=f'{folder}/{split}.jpg',split=split,label=label,pixels_sha256=f'{split}:{label}',group=f'{split}:{label}'))
        manifest=dict(schema=1,classes=list(DATASET_CLASSES),records=rows)
        static_data.validate_manifest(manifest)
        for folder in ('J','Z','del','space'):
            bad=copy.deepcopy(manifest);bad['records'][0]['path']=folder+'/x.jpg'
            with self.assertRaises(ValueError): static_data.validate_manifest(bad)
        bad=copy.deepcopy(manifest);bad['records'][25]['group']=rows[0]['group']
        with self.assertRaises(ValueError): static_data.validate_manifest(bad)

    def test_calibration_uses_all_classes_and_train_only(self):
        camera=[dict(path=f'camera/{split}/{label}',label=label,split=split) for split in ('train','val') for label in LEGACY_CLASSES]
        selected={f'kaggle_{split}':[dict(path=f'{split}/{c}',label=c,split=split) for c in DATASET_CLASSES] for split in ('train','val','test')}
        selected.update(camera_train=camera[:7],camera_val=camera[7:])
        manifest=dict(records=sum((selected[f'kaggle_{s}'] for s in ('train','val','test')),[]))
        training,_,rehearsal=static_export.calibration_records(selected,manifest,camera)
        self.assertEqual(set(r['label'] for r in training+rehearsal),set(STATIC_CLASSES))
        self.assertTrue(all(r['split']=='train' for r in training+rehearsal))
        bad=copy.deepcopy(selected);bad['kaggle_train'][0]=bad['kaggle_val'][0]
        with self.assertRaises(ValueError): static_export.calibration_records(bad,manifest,camera)
        bad=copy.deepcopy(selected);bad['kaggle_train']=[r for r in bad['kaggle_train'] if r['label']!='G']
        with self.assertRaises(ValueError): static_export.calibration_records(bad,manifest,camera)


@unittest.skipUnless(shutil.which('c++'), 'Host compiler required')
class StaticNativeTests(unittest.TestCase):
    def test_all_glyphs_indices_bounds_stability_and_cpp_python_gate_agreement(self):
        with tempfile.TemporaryDirectory() as directory:
            exe=Path(directory)/'static'
            includes=[arg for p in ('firmware/asl_live/include','firmware/camera_overlay/include') for arg in ('-I',str(ROOT/p))]
            subprocess.run(['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror',*includes,
                            str(ROOT/'tests/native/asl_static_display_test.cpp'),'-o',str(exe)],check=True,capture_output=True)
            subprocess.run([str(exe)],check=True,capture_output=True)
            reference=[3.,-120.,-90.,-130.,-40.,-170.,-40.]+[-40.]*19
            cases=[]
            for steps in (0,3,8,9):
                actual=reference.copy();actual[2]+=steps*.125
                cases.append((actual,reference,.125,steps<=8))
            for best in range(26):
                actual=[-40.]*26;actual[best]=20.
                cases.append((actual,actual,.125,True))
            cases.append(([float('nan'),*reference[1:]],reference,.125,False))
            # Top-1 unchanged, cosine extremely high, but 0.80 display flips.
            cases.append(([1.5,0.]+[-40.]*24,[1.375,0.]+[-40.]*24,.125,False))
            cases.append(([2.875,3.]+[-40.]*24,[3.,2.875]+[-40.]*24,.125,False))
            rng=random.Random(2026)
            for _ in range(50):
                expected=[rng.uniform(-20,20) for _ in range(26)]
                actual=[v+rng.uniform(-1,1) for v in expected]
                cases.append((actual,expected,.125,None))
            for actual,expected,scale,wanted in cases:
                payload=struct.pack('<53f',*actual,*expected,scale)
                unpacked=struct.unpack('<53f',payload)
                passed=asl_selftest.compare(unpacked[:26],unpacked[26:52],asl_selftest.policy(scale),classes=STATIC_CLASSES)
                result=subprocess.run([str(exe),'--compare'],input=payload,capture_output=True,check=True)
                self.assertEqual(result.stdout.strip(),b'1' if passed else b'0')
                if wanted is not None:self.assertEqual(passed,wanted)
