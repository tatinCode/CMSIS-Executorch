import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import personal_asl_firmware as firmware, asl_live_firmware as baseline
try:
    from PIL import Image
    from ml.asl import personal_data as data, personal_export
    import test_collect_camera as capture_tests
    from ml.asl.collection_data import audit
    from ml.asl.data import sha256
    HAS_TORCH=True
except ImportError:HAS_TORCH=False


class PersonalBuildTests(unittest.TestCase):
    def test_old_static_export_or_claimed_new_validation_cannot_be_personal_package(self):
        from tools.inference import SetupError
        value=dict(personal_trial=True,new_camera_validation=False,new_training_count=29,
                   new_camera_training_fit_only=dict(float=dict(count=29),pt2e=dict(count=29)))
        self.assertEqual(firmware.validate_personal_export(value),value)
        for bad in ({},{**value,'personal_trial':False},{**value,'new_camera_validation':True},
                    {**value,'new_training_count':0},{**value,'new_camera_training_fit_only':{}}):
            with self.assertRaises(SetupError):firmware.validate_personal_export(bad)

    def test_private_builder_does_not_change_static_live_workspace_or_source_selector(self):
        before=(baseline.WORKSPACE,baseline.source_paths,baseline.LIVE.copy())
        builder=firmware.private_builder()
        self.assertNotEqual(builder.WORKSPACE,baseline.WORKSPACE)
        self.assertEqual(before,(baseline.WORKSPACE,baseline.source_paths,baseline.LIVE))
        self.assertIn(Path(firmware.__file__),builder.source_paths())


@unittest.skipUnless(HAS_TORCH,'Isolated torch environment required')
class PersonalDataTests(unittest.TestCase):
    def test_leakage_quarantines_new_train_not_preserved_validation(self):
        old_train=[dict(path='oldtrain',label='A',normalized_sha256='train',dhash=3)]
        old_val=[dict(path='oldval',label='A',normalized_sha256='val',dhash=0)]
        candidates=[dict(path='leak',label='B',normalized_sha256='new',dhash=1),
                    dict(path='duplicate',label='A',normalized_sha256='train',dhash=3),
                    dict(path='usable',label='G',normalized_sha256='good',dhash=(1<<64)-1)]
        val_before=copy.deepcopy(old_val)
        kept,quarantine=data.screen_new(old_train,old_val,candidates)
        self.assertEqual([r['path'] for r in kept],['usable'])
        self.assertEqual(len(quarantine),2)
        self.assertEqual(old_val,val_before)
        self.assertEqual(quarantine[0]['reason'],'matches_preserved_legacy_validation')

    def test_reviewed_merge_preserves_old_val_and_freezes_completed_pilot_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);captures=repo/'captures'
            capture_tests.CollectionAuditTests().session(captures)
            audited=repo/'audit'
            audit(argparse.Namespace(root=captures,output=audited,train_person=[],val_person=[],test_person=[]))
            review=repo/'review.json'
            review.write_text(json.dumps(dict(schema=1,status='reviewed',collection_manifest_sha256=sha256(audited/'manifest.json'),excluded=[])))
            old_root=repo/'old';old_root.mkdir()
            rows=[]
            for split,color in (('train','red'),('val','blue')):
                path=old_root/(split+'.png');Image.new('RGB',(128,128),color).save(path)
                rows.append(dict(path=path.name,session='old-'+split,label='A',split=split,sha256=sha256(path),
                    normalized_sha256=color,dhash=0xffffffffffffffff if split=='val' else 0x5555555555555555))
            active=captures/'p01'/'active';active.mkdir();(active/'session.json').write_text('{}')
            with mock.patch.object(data.common,'ROOT',repo),mock.patch.object(data,'load_camera_data',return_value=(old_root,rows,{},{})):
                merged,screening=data.merge(repo/'legacy.json',repo/'old-review.json',audited/'manifest.json',review)
                self.assertEqual(screening['new_training_count'],1)
                self.assertEqual([(r['origin'],r['split']) for r in merged],
                                 [('legacy','train'),('continuous_pilot','train'),('legacy','val')])
                self.assertEqual(merged[-1]['sha256'],rows[-1]['sha256'])
                # A tampered exclusion must not silently remove an image.
                bad=json.loads(review.read_text());bad['excluded']=[dict(path='not-a-record',sha256='bad',reason='x')]
                review.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):data.merge(repo/'legacy.json',repo/'old-review.json',audited/'manifest.json',review)

    def test_personal_export_uses_private_loader_and_rejects_unchanged_or_regressing_head(self):
        from ml.asl import static_export
        original=static_export.load_camera_data
        engine=personal_export.private_exporter()
        self.assertIs(static_export.load_camera_data,original)
        self.assertIs(engine.load_camera_data,data.load_personal_data)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);checkpoint=root/'best.pth';checkpoint.write_bytes(b'not loaded')
            for flags in (dict(adapted=False,camera_nonregression_passed=True),dict(adapted=True,camera_nonregression_passed=False)):
                (root/'metrics.json').write_text(json.dumps(flags))
                with self.assertRaises(ValueError):personal_export.run(argparse.Namespace(checkpoint=checkpoint,output=root/'new'))
