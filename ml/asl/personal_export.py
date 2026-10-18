"""Private static-export engine for reviewed personal TRAIN + preserved VAL."""

import argparse
import importlib.util
import json
from pathlib import Path

from . import static_export
from .camera_data import write_json
from .data import sha256
from .personal_data import load_personal_data


def private_exporter():
    spec=importlib.util.spec_from_file_location('ml.asl._personal_export_engine',static_export.__file__)
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.load_camera_data=load_personal_data
    return engine


def run(args):
    if args.output.exists():raise ValueError('Choose a new personal export directory')
    metrics=json.loads(args.checkpoint.with_name('metrics.json').read_text())
    if metrics.get('adapted') is not True or metrics.get('camera_nonregression_passed') is not True:
        raise ValueError('No improved camera-nonregressing personal checkpoint selected; retain the baseline')
    engine=private_exporter()
    checkpoint=engine.torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    config=checkpoint['configuration']
    root,records,manifest,_=load_personal_data(Path(config['camera_manifest']),Path(config['label_review']))
    new_rows=[r for r in records if r['split']=='train' and r['origin']=='continuous_pilot']
    validation=[r for r in records if r['split']=='val']
    original_score=engine.score;reports={};outputs={};phase=0

    def score(model,source,rows):
        nonlocal phase
        result=original_score(model,source,rows)
        if Path(source)==root and rows==validation:
            name=('float','quantized','reloaded')[phase]
            logits,report,predictions=original_score(model,root,new_rows)
            reports[name]=report;outputs[name]=logits
            if phase==2 and float((logits-outputs['quantized']).abs().max())!=0:
                raise ValueError('Quantized reload changed NEW TRAINING diagnostic logits')
            write_json(args.output/(name+'-new-training-fit-NOT-validation.json'),
                       dict(metrics=report,predictions=predictions,held_out=False,purpose='TRAINING FIT ONLY; not recognition accuracy'))
            phase+=1
        return result

    original_write=engine.write_json
    def write(path,value):
        if Path(path)==args.output/'manifest.json':
            if phase!=3:raise ValueError('Missing personal float/int8/reload diagnostics')
            value.update(personal_trial=True,new_camera_validation=False,
                new_camera_training_fit_only=dict(float=reports['float'],pt2e=reports['quantized']),
                camera_untrained_letters=manifest['camera_untrained_letters'],
                new_training_count=manifest['screening']['new_training_count'],
                legacy_validation_count=manifest['screening']['old_validation_count'],
                dataset_test_evaluated=False)
            value['limitations']=[
                'All new pilot captures are TRAIN only. Training-fit scores do NOT establish new-letter camera recognition.',
                f"{len(manifest['camera_unvalidated_letters'])} letters have no held-out camera examples; {len(manifest['camera_untrained_letters'])} have no camera training examples.",
                'Old validation has influenced prior models; this is development evidence, not a final test or unseen-person benchmark.',
                'Threshold 0.80 is provisional. PT2E simulation is not Vela/device execution.']
        original_write(path,value)

    engine.score,engine.write_json=score,write
    engine.run(args)


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--checkpoint',type=Path,default=Path('.work/asl/personal-train-v2/best.pth'))
    parser.add_argument('--output',type=Path,default=Path('.work/asl/personal-export-v1'))
    run(parser.parse_args())


if __name__=='__main__':main()
