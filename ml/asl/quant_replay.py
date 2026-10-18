"""Head-only quantization-aware replay on the frozen, calibrated PT2E backbone."""

import argparse
import copy
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler
from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e  # Register serialized quantized ops.

from tools.asl_contract import STATIC_CLASSES
from .adapt import balanced_loss, sampling_weights
from .camera_data import write_json
from .data import sha256
from .model import load_checkpoint
from .personal_data import load_personal_data
from .replay_train import FLOORS, protected_gate
from .static_data import StaticImages, decisions, select_records, validate_manifest


def backbone(graph):
    result = copy.deepcopy(graph)
    linear = [n for n in result.graph.nodes if n.target == torch.ops.aten.linear.default]
    if len(linear) != 1:
        raise ValueError('Expected exactly one classifier')
    activation = linear[0].args[0]
    scale = activation.args[1]
    output = next(n for n in result.graph.nodes if n.op == 'output')
    # Exported GraphModules retain a pytree output spec expecting a tuple.
    # Returning a bare tensor makes tree_unflatten consume its batch dimension.
    output.args = ((activation,),)
    result.graph.eliminate_dead_code()
    result.graph.lint()
    result.recompile()
    return result, scale


def rounded(value, scale):
    return value + ((value / scale).round() * scale - value).detach()


class QuantHead(torch.nn.Module):
    def __init__(self, linear, input_scale):
        super().__init__()
        self.linear = copy.deepcopy(linear)
        self.input_scale = input_scale
        self.scale, self.zero = 1., 0

    def raw(self, x):
        weight = self.linear.weight
        scales = weight.detach().abs().amax(1).clamp_min(torch.finfo(torch.float32).eps) / 127
        quant = torch.fake_quantize_per_channel_affine(weight, scales, torch.zeros(len(scales), dtype=torch.int32), 0, -127, 127)
        bias = rounded(self.linear.bias, self.input_scale * scales)
        return torch.nn.functional.linear(x, quant, bias)

    @torch.no_grad()
    def observe_train(self, calibration):
        logits = self.raw(calibration)
        low, high = min(float(logits.min()), 0.), max(float(logits.max()), 0.)
        self.scale = max((high-low)/255, torch.finfo(torch.float32).eps)
        self.zero = max(-128, min(127, -128-round(low/self.scale)))

    def forward(self, x):
        return torch.fake_quantize_per_tensor_affine(self.raw(x), self.scale, self.zero, -128, 127)


def run(args):
    if args.output.exists():
        raise ValueError('Choose a fresh output directory')
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(2026)
    torch.use_deterministic_algorithms(True)
    checkpoint_path = Path('.work/asl/replay-train-v3/best.pth')
    model, checkpoint = load_checkpoint(checkpoint_path)
    config = checkpoint['configuration']
    root, camera, manifest, _ = load_personal_data(Path(config['camera_manifest']), Path(config['label_review']))
    source_path = Path(config['source_manifest'])
    source = json.loads(source_path.read_text())
    validate_manifest(source)
    selected = dict(camera_train=[r for r in camera if r['split']=='train'], camera_val=[r for r in camera if r['split']=='val'],
        **{'kaggle_'+s:select_records(source,s,120 if s=='train' else 100,2026) for s in ('train','val','test')})
    export = Path('.work/asl/replay-export-v1')
    original = json.loads((export/'manifest.json').read_text())
    if original['checkpoint_sha256'] != sha256(checkpoint_path) or original['artifact_sha256']['quantized.pt2'] != sha256(export/'quantized.pt2'):
        raise ValueError('Quantized graph provenance differs from checkpoint')
    quant = torch.export.load(export/'quantized.pt2').module(check_guards=False)
    feature_model, input_scale = backbone(quant)
    args.output.mkdir(parents=True)
    identities = {str(p):sha256(p) for p in (checkpoint_path, export/'quantized.pt2', export/'manifest.json',
        Path(config['camera_manifest']), Path(config['label_review']), source_path, *Path(__file__).parent.glob('*.py'))}
    write_json(args.output/'selected.json', selected)
    def extract(key, augmented=False):
        directory = root if key.startswith('camera_') else Path(source['root'])
        records = selected[key]
        if any(sha256(directory/r['path']) != r['sha256'] for r in records):
            raise ValueError('Image changed')
        values, labels = [], []
        with torch.no_grad():
            for x,y in DataLoader(StaticImages(directory, records, augmented),batch_size=1):
                values.append(feature_model(x).detach().clone()); labels.append(y)
        print('quantized features:',key,'augmented',augmented,len(values),flush=True)
        x,y = torch.cat(values),torch.cat(labels)
        if x.shape != (len(records),1280):
            raise ValueError('Quantized feature shape lost batch dimension')
        torch.save(dict(x=x,y=y),args.output/(key+('-augmented' if augmented else '')+'-features.pt'))
        return x,y
    bx,by = extract('camera_train')
    ax,ay = extract('camera_train',True)
    kx,ky = extract('kaggle_train')
    vx,vy = extract('camera_val')
    kvx,kvy = extract('kaggle_val')
    head = QuantHead(model.classifier[1], input_scale)
    # Verify this head simulation against the untouched serialized graph before fitting.
    output_quant = [n for n in quant.graph.nodes if 'quantize_per_tensor' in str(n.target) and 'dequantize' not in str(n.target)][-1]
    head.scale,head.zero = output_quant.args[1:3]
    sample = StaticImages(root, selected['camera_val'])[0][0].unsqueeze(0)
    with torch.no_grad():
        difference = float((head(feature_model(sample))-quant(sample)).abs().max())
    if difference > 1e-4:
        raise ValueError('Head fake-quantization differs from real PT2E graph: '+str(difference))
    calibration = torch.cat((bx,torch.cat([kx[[i for i,r in enumerate(selected['kaggle_train']) if r['label']==c][:10]] for c in STATIC_CLASSES[:-1]])))
    anchors = bx[[i for i,r in enumerate(selected['camera_train']) if r['origin']=='legacy']]
    with torch.no_grad():
        teacher = head(anchors).detach().clone()
    tx,ty = torch.cat((bx,ax,kx)),torch.cat((by,ay,ky))
    sampler = WeightedRandomSampler(sampling_weights(ty,len(by)+len(ay),0.9),8192,replacement=True,
        generator=torch.Generator().manual_seed(2026))
    loader = DataLoader(TensorDataset(tx,ty),batch_size=32,sampler=sampler)
    optimizer = torch.optim.Adam(head.parameters(),lr=0.0001,weight_decay=1e-4)
    targets = [r['label'] for r in selected['camera_val']]
    with torch.no_grad():
        baseline,_ = decisions(head(vx),targets)
    best, state, epoch_best, history = float('inf'),None,None,[]
    for epoch in range(1,61):
        head.observe_train(calibration)
        for x,y in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(head(x),y) + 2*torch.nn.functional.mse_loss(head(anchors),teacher)
            loss.backward(); optimizer.step()
        head.observe_train(calibration)
        with torch.no_grad():
            logits,kl = head(vx),head(kvx)
            report,_ = decisions(logits,targets)
            score = (balanced_loss(logits,vy)+balanced_loss(kl,kvy))/2
        eligible = protected_gate(baseline,report)
        history.append(dict(epoch=epoch,eligible=eligible,loss=score,camera=report))
        if eligible and score < best:
            best,state,epoch_best = score,copy.deepcopy(head.linear.state_dict()),epoch
        print('epoch',epoch,'eligible',eligible,{c:report['per_class'][c]['correct_display_count'] for c in FLOORS},flush=True)
    write_json(args.output/'history.json',history)
    if state is None:
        raise ValueError('Quantization-aware candidate failed protected letter checks; no model released')
    model.classifier[1].load_state_dict(state)
    config = {**config, 'input_sha256':identities, 'training_per_class':120, 'eval_per_class':100,
        'quantization_aware_head':True,'frozen_quantized_backbone':str(export/'quantized.pt2'),
        'head_simulation_initial_max_difference':difference,'selection':'minimum quantized-feature development loss subject to unchanged protected gates',
        'camera_unvalidated_letters':manifest['camera_unvalidated_letters']}
    checkpoint.update(configuration=config,state_dict=model.state_dict(),best_epoch=epoch_best)
    path = args.output/'best.pth'
    torch.save(checkpoint,path)
    if any(sha256(p)!=h for p,h in identities.items()):
        raise ValueError('Dependency changed')
    write_json(args.output/'config.json',config)
    write_json(args.output/'metrics.json',dict(checkpoint_sha256=sha256(path),adapted=True,camera_nonregression_passed=True,
        frozen_backbone_verified=True,final_test_evaluated=False,best_epoch=epoch_best,
        numerical_domain='Fake-quantized head on frozen PT2E backbone; fresh full export acceptance still required',
        hardware_verified=False,deployment_approved=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--output',type=Path,default=Path('.work/asl/quant-replay-train-v1'))
    run(parser.parse_args())


if __name__ == '__main__':
    main()
