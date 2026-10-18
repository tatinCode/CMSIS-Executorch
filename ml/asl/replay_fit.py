"""Restore the old A row by label; fit other outputs using verified replay features."""

import argparse
import copy
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler

from tools.asl_contract import STATIC_CLASSES
from .adapt import balanced_loss, sampling_weights
from .camera_data import write_json
from .data import sha256
from .model import load_checkpoint
from .personal_data import load_personal_data
from .replay_train import protected_gate, FLOORS
from .static_data import StaticImages, decisions


def run(args):
    if args.output.exists():
        raise ValueError('Choose a fresh output directory')
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(2026)
    torch.use_deterministic_algorithms(True)
    previous = json.loads((args.source / 'config.json').read_text())
    for p, h in previous['input_sha256'].items():
        if sha256(p) != h:
            raise ValueError('Replay training dependency changed: ' + p)
    root, camera, _, _ = load_personal_data(Path(previous['camera_manifest']), Path(previous['label_review']))
    selected = json.loads((args.source / 'selected.json').read_text())
    if selected['camera_train'] != [r for r in camera if r['split'] == 'train'] or selected['camera_val'] != [r for r in camera if r['split'] == 'val']:
        raise ValueError('Camera selection changed')
    cache = args.source / 'features'
    cache_files = list(cache.glob('*.pt'))
    identities = {**previous['input_sha256'], **{str(p):sha256(p) for p in
        (*cache_files, args.source/'config.json', args.source/'selected.json', args.teacher, Path(__file__))}}
    def read(name):
        value = torch.load(cache / name, map_location='cpu', weights_only=True)
        return value['x'], value['y']
    bx, by = read('camera_train-base-0.pt')
    camera_parts = [(bx, by)] + [read(f'camera_train-augmented-{i}.pt') for i in range(3)]
    kaggle_parts = [read('kaggle_train-base-0.pt'), read('kaggle_train-augmented-0.pt')]
    cx, cy = (torch.cat([v[i] for v in camera_parts]) for i in (0,1))
    kx, ky = (torch.cat([v[i] for v in kaggle_parts]) for i in (0,1))
    vx, vy = read('camera_val-base-0.pt')
    kvx, kvy = read('kaggle_val-base-0.pt')
    for key, parts in [('camera_train',camera_parts),('kaggle_train',kaggle_parts),('camera_val',[(vx,vy)]),('kaggle_val',[(kvx,kvy)])]:
        expected = torch.tensor([STATIC_CLASSES.index(r['label']) for r in selected[key]])
        for x,y in parts:
            if not torch.equal(y,expected) or x.shape != (len(y),1280) or not torch.isfinite(x).all():
                raise ValueError('Cached feature labels/shape/values differ from selected rows')
    model, checkpoint = load_checkpoint(Path(previous['checkpoint']))
    teacher, teacher_meta = load_checkpoint(args.teacher)
    if any(not torch.equal(v,teacher.features.state_dict()[k]) for k,v in model.features.state_dict().items()):
        raise ValueError('Teacher and replay feature backbones differ')
    original_backbone = copy.deepcopy(model.features.state_dict())
    targets = [r['label'] for r in selected['camera_val']]
    kaggle_targets = [r['label'] for r in selected['kaggle_val']]
    with torch.no_grad():
        baseline,_ = decisions(model.classifier(vx),targets)
    row = STATIC_CLASSES.index('A')
    old_row = teacher_meta['classes'].index('A')
    with torch.no_grad():
        model.classifier[1].weight[row].copy_(teacher.classifier[1].weight[old_row])
        model.classifier[1].bias[row].copy_(teacher.classifier[1].bias[old_row])
    fixed_rows = [STATIC_CLASSES.index(c) for c in args.freeze_labels]
    fixed_weight = model.classifier[1].weight[fixed_rows].detach().clone()
    fixed_bias = model.classifier[1].bias[fixed_rows].detach().clone()
    legacy_indices = [i for i,r in enumerate(selected['camera_train']) if r['origin'] == 'legacy']
    anchors = bx[legacy_indices]
    with torch.no_grad():
        anchor_logits = model.classifier(anchors).detach().clone()
    model.requires_grad_(False)
    model.classifier.requires_grad_(True)
    tx,ty = torch.cat((cx,kx)),torch.cat((cy,ky))
    sampler = WeightedRandomSampler(sampling_weights(ty,len(cy),0.9),8192,replacement=True,
        generator=torch.Generator().manual_seed(2026))
    loader = DataLoader(TensorDataset(tx,ty),batch_size=32,sampler=sampler)
    optimizer = torch.optim.Adam(model.classifier.parameters(),lr=args.learning_rate,weight_decay=1e-4)
    best, state, best_epoch, history = float('inf'), None, None, []
    for epoch in range(1,61):
        model.classifier.train()
        for x,y in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model.classifier(x),y)
            if args.anchor_weight:
                # Original TRAIN examples only. Disable dropout for reproducible
                # logit preservation; validation never participates in this loss.
                model.classifier.eval()
                loss = loss + args.anchor_weight * torch.nn.functional.mse_loss(model.classifier(anchors),anchor_logits)
                model.classifier.train()
            loss.backward()
            model.classifier[1].weight.grad[fixed_rows] = 0
            model.classifier[1].bias.grad[fixed_rows] = 0
            optimizer.step()
            with torch.no_grad():
                model.classifier[1].weight[fixed_rows] = fixed_weight
                model.classifier[1].bias[fixed_rows] = fixed_bias
        model.eval()
        with torch.no_grad():
            logits, kl = model.classifier(vx),model.classifier(kvx)
            report,_ = decisions(logits,targets)
            loss = (balanced_loss(logits,vy)+balanced_loss(kl,kvy))/2
        eligible = protected_gate(baseline,report)
        if eligible and loss < best:
            best,state,best_epoch = loss,copy.deepcopy(model.classifier.state_dict()),epoch
        history.append(dict(epoch=epoch,loss=loss,eligible=eligible,camera=report))
        print(epoch,eligible,report['correct_letter_count'],report['wrong_letter_count'],
              {c:report['per_class'][c]['correct_display_count'] for c in FLOORS},flush=True)
    args.output.mkdir(parents=True)
    write_json(args.output/'history.json',history)
    if state is None:
        raise ValueError('No trained epoch passes protected letter checks; no checkpoint released')
    model.classifier.load_state_dict(state)
    model.eval()
    if any(not torch.equal(v,model.features.state_dict()[k]) for k,v in original_backbone.items()):
        raise ValueError('Backbone changed')
    if not torch.equal(model.classifier[1].weight[fixed_rows],fixed_weight) or not torch.equal(model.classifier[1].bias[fixed_rows],fixed_bias):
        raise ValueError('Protected head rows changed')
    with torch.no_grad():
        direct = torch.cat([model(x) for x,_ in DataLoader(StaticImages(root,selected['camera_val']),batch_size=32)])
        delta = float((direct-model.classifier(vx)).abs().max())
        report, predictions = decisions(direct,targets)
        kaggle,_ = decisions(model.classifier(kvx),kaggle_targets)
        fit,_ = decisions(model.classifier(bx),[r['label'] for r in selected['camera_train']])
    if delta > 1e-4 or not protected_gate(baseline,report):
        raise ValueError('Full-model verification failed protected selection')
    if any(sha256(p)!=h for p,h in identities.items()):
        raise ValueError('Replay dependencies changed during fitting')
    config = {**previous, 'output':str(args.output), 'input_sha256':identities,
        'learning_rate':args.learning_rate, 'freeze_labels':args.freeze_labels,
        'anchor_weight':args.anchor_weight, 'anchor_origin':'legacy TRAIN ONLY',
        'protected_display_floors':FLOORS, 'fixed_A_teacher':str(args.teacher),
        'selection':'minimum joint development loss subject to A/L/Y floors and aggregate nonregression; old A row fixed by label'}
    checkpoint.update(state_dict=model.state_dict(),configuration=config,best_epoch=best_epoch)
    path = args.output/'best.pth'
    torch.save(checkpoint,path)
    reloaded,_ = load_checkpoint(path)
    if any(not torch.equal(v,reloaded.state_dict()[k]) for k,v in model.state_dict().items()):
        raise ValueError('Checkpoint reload differs')
    write_json(args.output/'config.json',config)
    write_json(args.output/'selected.json',selected)
    write_json(args.output/'camera-validation.json',dict(metrics=report,predictions=predictions))
    write_json(args.output/'metrics.json',dict(checkpoint_sha256=sha256(path),adapted=True,
        camera_nonregression_passed=True,final_test_evaluated=False,frozen_backbone_verified=True,
        best_epoch=best_epoch,camera_validation=report,kaggle_validation=kaggle,
        all_camera_training_fit_only=fit,restored_A_row_exact=True,full_model_logit_difference=delta,
        hardware_verified=False,deployment_approved=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--source',type=Path,default=Path('.work/asl/replay-train-v1'))
    parser.add_argument('--teacher',type=Path,default=Path('.work/asl/adapt-v1/best.pth'))
    parser.add_argument('--output',type=Path,default=Path('.work/asl/replay-train-v3'))
    parser.add_argument('--learning-rate',type=float,default=0.0003)
    parser.add_argument('--anchor-weight',type=float,default=2.0)
    parser.add_argument('--freeze-labels',nargs='+',choices=STATIC_CLASSES,default=['A','L','Y'])
    args = parser.parse_args()
    if (not 0 < args.learning_rate < float('inf') or not 0 <= args.anchor_weight < float('inf') or
            'A' not in args.freeze_labels or len(set(args.freeze_labels)) != len(args.freeze_labels)):
        parser.error('Invalid learning rate, anchor weight or protected rows')
    run(args)


if __name__ == '__main__':
    main()
