"""Head-only solo-presenter trial; new pilot images are TRAIN, old VAL stays held out."""

import argparse
import copy
import json
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler

from tools import inference as common
from tools.asl_contract import STATIC_CLASSES
from .adapt import sampling_weights, balanced_loss
from .camera_data import write_json
from .data import sha256
from .export import quantization_gate
from .model import load_checkpoint, contract
from .personal_data import load_personal_data
from .static_data import StaticImages, select_records, validate_manifest, decisions
from .train import features


def train(args):
    if args.output.exists():raise ValueError('Choose a new personal training directory')
    torch.set_num_threads(args.threads);torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed);random.seed(args.seed);np.random.seed(args.seed)
    torch.use_deterministic_algorithms(True)
    started=time.monotonic()
    camera_root,camera,camera_manifest,_=load_personal_data(args.camera_manifest,args.label_review)
    source=json.loads(args.manifest.read_text());validate_manifest(source);source_root=Path(source['root'])
    model,initial=load_checkpoint(args.checkpoint)
    if initial['schema']!=3 or initial['preprocessing']!=contract(128):raise ValueError('Start from the preserved static-26 checkpoint')
    model.requires_grad_(False);model.classifier.requires_grad_(True);model.eval()
    original={k:v.detach().clone() for k,v in model.state_dict().items()}
    selected=dict(camera_train=[r for r in camera if r['split']=='train'],camera_val=[r for r in camera if r['split']=='val'],
        **{'kaggle_'+s:select_records(source,s,args.train_per_class if s=='train' else args.eval_per_class,args.seed) for s in ('train','val','test')})
    for key,rows in selected.items():
        root=camera_root if key.startswith('camera_') else source_root
        if any(sha256(root/r['path'])!=r['sha256'] for r in rows):raise ValueError('Selected source image changed')
    bound=(args.camera_manifest,args.label_review,args.manifest,args.checkpoint,
           *(Path(__file__).parent/n for n in ('personal_train.py','personal_data.py','collection_data.py','model.py','static_data.py',
                                                'train.py','data.py','adapt.py','camera_data.py','export.py')),
           common.ROOT/'tools/asl_contract.py',common.ROOT/'tools/collect_camera.py')
    identities={str(p):sha256(p) for p in bound}
    args.output.mkdir(parents=True)
    config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}
    config.update(classes=list(STATIC_CLASSES),preprocessing=contract(128),input_sha256=identities,
        source_manifest=str(args.manifest),camera_manifest=str(args.camera_manifest),label_review=str(args.label_review),
        training_per_class=args.train_per_class,eval_per_class=args.eval_per_class,frozen_backbone=True,
        threshold=0.8,threshold_calibrated=False,final_test_evaluated=False,dataset_test_evaluated=False,
        camera_untrained_letters=camera_manifest['camera_untrained_letters'],
        camera_unvalidated_letters=camera_manifest['camera_unvalidated_letters'],
        selection=('minimum mean camera/Kaggle development balanced CE among TRAINED camera count-nonregressing epochs; explicit experimental trial, not loss nonregression'
                   if args.adapted_trial else 'minimum mean camera/Kaggle development balanced CE among camera count-nonregressing epochs, including unchanged epoch 0'),
        camera_nonregression='correct displays >= initial; wrong displays <= initial; negative false letters <= initial',
        new_pilot_evaluation='TRAINING FIT ONLY; not held-out recognition accuracy')
    write_json(args.output/'config.json',config);write_json(args.output/'selected.json',selected)

    def extract(key,augment=False):
        root=camera_root if key.startswith('camera_') else source_root
        return features(model,StaticImages(root,selected[key],augment),args.batch_size,key+('/augmented' if augment else '/base'))

    base_x,base_y=extract('camera_train');cx,cy=base_x,base_y
    for _ in range(3):
        x,y=extract('camera_train',True);cx,cy=torch.cat((cx,x)),torch.cat((cy,y))
    kx,ky=extract('kaggle_train');x,y=extract('kaggle_train',True)
    kx,ky=torch.cat((kx,x)),torch.cat((ky,y))
    vx,vy=extract('camera_val');kvx,kvy=extract('kaggle_val')
    new_indices=[i for i,r in enumerate(selected['camera_train']) if r['origin']=='continuous_pilot']
    new_labels=[selected['camera_train'][i]['label'] for i in new_indices]
    camera_targets=[r['label'] for r in selected['camera_val']]
    kaggle_targets=[r['label'] for r in selected['kaggle_val']]
    with torch.no_grad():
        baseline_logits=model.classifier(vx);baseline_kaggle=model.classifier(kvx)
        baseline,_=decisions(baseline_logits,camera_targets)
        initial_fit,_=decisions(model.classifier(base_x[new_indices]),new_labels)
        baseline_selection_loss=(balanced_loss(baseline_logits,vy)+balanced_loss(baseline_kaggle,kvy))/2
        best=float('inf') if args.adapted_trial else baseline_selection_loss
    best_epoch=0;state=copy.deepcopy(model.classifier.state_dict());history=[]
    tx,ty=torch.cat((cx,kx)),torch.cat((cy,ky))
    sampler=WeightedRandomSampler(sampling_weights(ty,len(cy),args.camera_fraction),args.samples_per_epoch,
        replacement=True,generator=torch.Generator().manual_seed(args.seed))
    loader=DataLoader(TensorDataset(tx,ty),batch_size=args.batch_size,sampler=sampler)
    optimizer=torch.optim.Adam(model.classifier.parameters(),lr=args.learning_rate,weight_decay=1e-4)
    for epoch in range(1,args.epochs+1):
        model.classifier.train()
        for x,y in loader:
            optimizer.zero_grad(set_to_none=True)
            loss=torch.nn.functional.cross_entropy(model.classifier(x),y);loss.backward();optimizer.step()
        model.eval()
        with torch.no_grad():
            logits,klogits=model.classifier(vx),model.classifier(kvx)
            score=(balanced_loss(logits,vy)+balanced_loss(klogits,kvy))/2
            cm,_=decisions(logits,camera_targets);km,_=decisions(klogits,kaggle_targets)
        eligible=quantization_gate(baseline,cm)
        if eligible and score<best:best,best_epoch,state=score,epoch,copy.deepcopy(model.classifier.state_dict())
        history.append(dict(epoch=epoch,selection_loss=score,camera_nonregression=eligible,camera=cm,kaggle=km))
        print(f"epoch={epoch} eligible={eligible} selection={score:.4f} camera={cm['correct_letter_count']}/30 "
              f"wrong={cm['wrong_letter_count']} negative_false={cm['negative_false_letter_count']}",flush=True)
    if best_epoch==0:best=baseline_selection_loss
    model.classifier.load_state_dict(state);model.eval()
    if any(not torch.equal(v,model.state_dict()[k]) for k,v in original.items() if not k.startswith('classifier.')):
        raise ValueError('Frozen backbone/BatchNorm tensors changed')
    with torch.no_grad():
        cached=model.classifier(vx)
        cm,cr=decisions(cached,camera_targets)
        km,kr=decisions(model.classifier(kvx),kaggle_targets)
        fit,fr=decisions(model.classifier(base_x[new_indices]),new_labels)
    checkpoint=args.output/'best.pth'
    torch.save(dict(schema=3,architecture='mobilenet_v2',classes=list(STATIC_CLASSES),preprocessing=contract(128),
        state_dict=model.state_dict(),best_epoch=best_epoch,configuration=config),checkpoint)
    reloaded,_=load_checkpoint(checkpoint)
    if any(not torch.equal(v,reloaded.state_dict()[k]) for k,v in model.state_dict().items()):raise ValueError('Checkpoint tensor reload changed')
    with torch.no_grad():
        direct=torch.cat([reloaded(x) for x,_ in DataLoader(StaticImages(camera_root,selected['camera_val']),batch_size=args.batch_size)])
    delta=float((direct-cached).abs().max());direct_report,direct_rows=decisions(direct,camera_targets)
    if delta>1e-4 or [(r['display'],r['top_class']) for r in cr]!=[(r['display'],r['top_class']) for r in direct_rows]:
        raise ValueError('Full model reload disagrees with cached-feature decisions')
    for name,report,rows in (('camera-validation',direct_report,direct_rows),('kaggle-validation',km,kr),('new-camera-training-fit-NOT-validation',fit,fr)):
        write_json(args.output/(name+'.json'),dict(metrics=report,predictions=rows))
    if any(sha256(p)!=h for p,h in identities.items()):raise ValueError('Training dependencies changed during fitting')
    load_personal_data(args.camera_manifest,args.label_review)
    for key,rows in selected.items():
        root=camera_root if key.startswith('camera_') else source_root
        if any(sha256(root/r['path'])!=r['sha256'] for r in rows):raise ValueError('Selected image changed during fitting')
    write_json(args.output/'history.json',history)
    write_json(args.output/'metrics.json',dict(schema=3,checkpoint_sha256=sha256(checkpoint),best_epoch=best_epoch,
        best_selection_loss=best,baseline_selection_loss=baseline_selection_loss,
        combined_loss_nonregression_passed=best<=baseline_selection_loss,
        baseline_camera_validation=baseline,camera_validation=direct_report,kaggle_validation=km,
        new_camera_training_fit_only=dict(before=initial_fit,after=fit),camera_nonregression_passed=quantization_gate(baseline,direct_report),
        adapted=best_epoch>0,frozen_backbone_verified=True,full_model_reload_max_logit_difference=delta,
        camera_unvalidated_letters=config['camera_unvalidated_letters'],final_test_evaluated=False,dataset_test_evaluated=False,
        hardware_verified=False,deployment_approved=False,elapsed_seconds=time.monotonic()-started))


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--camera-manifest',type=Path,default=Path('.work/asl/personal-camera-v3/manifest.json'))
    parser.add_argument('--label-review',type=Path,default=Path('.work/asl/personal-review-v1/label-review.json'))
    parser.add_argument('--manifest',type=Path,default=Path('.work/asl/static-dataset-v1/manifest.json'))
    parser.add_argument('--checkpoint',type=Path,default=Path('.work/asl/static-train-v2/best.pth'))
    parser.add_argument('--output',type=Path,default=Path('.work/asl/personal-train-v1'))
    parser.add_argument('--epochs',type=int,default=20)
    parser.add_argument('--threads',type=int,default=2)
    parser.add_argument('--batch-size',type=int,default=32)
    parser.add_argument('--train-per-class',type=int,default=600)
    parser.add_argument('--eval-per-class',type=int,default=100)
    parser.add_argument('--camera-fraction',type=float,default=0.5)
    parser.add_argument('--samples-per-epoch',type=int,default=8192)
    parser.add_argument('--learning-rate',type=float,default=0.0003)
    parser.add_argument('--seed',type=int,default=2026)
    parser.add_argument('--adapted-trial',action='store_true',help='Explicit trial: select a TRAINED camera-count-nonregressing epoch even if combined loss worsens')
    args=parser.parse_args()
    if (min(args.epochs,args.threads,args.batch_size,args.train_per_class,args.eval_per_class,args.samples_per_epoch)<1 or
            not 0<args.camera_fraction<1 or not 0<args.learning_rate<float('inf')):parser.error('Invalid counts or learning parameters')
    train(args)


if __name__=='__main__':main()
