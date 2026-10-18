"""Isolated 26-output PT2E/U85 export. TRAIN-only calibration; no flash or serial."""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import random
import time

import numpy as np
from PIL import Image
import torch

from tools import inference as common, inspect_asl_pte as inspector, asl_selftest
from tools.asl_contract import STATIC_CLASSES
from .adapt import load_camera_data
from .camera_data import write_json
from .data import sha256
from .export import final_output_scale, quantization_gate
from .model import load_checkpoint, transform, square_rgb
from .static_data import StaticImages, validate_manifest, select_records, decisions


def calibration_records(selected, manifest, camera):
    known = {r['path']: r for r in manifest['records']}
    training = [r for r in camera if r['split'] == 'train']
    validation = [r for r in camera if r['split'] == 'val']
    if selected['camera_train'] != training or selected['camera_val'] != validation:
        raise ValueError('Camera selection differs from reviewed sessions')
    for split in ('train', 'val', 'test'):
        rows = selected['kaggle_' + split]
        if (not rows or len({r['path'] for r in rows}) != len(rows) or
                any(r.get('split') != split or known.get(r['path']) != r for r in rows)):
            raise ValueError('Kaggle selection differs from its audited split')
    # Calibration is a deterministic, class-balanced subset of selected TRAIN.
    rehearsal = []
    for label in STATIC_CLASSES[:-1]:
        group = [r for r in selected['kaggle_train'] if r['label'] == label]
        if not group:
            raise ValueError('Calibration is missing a static letter or NO_HAND')
        rehearsal.extend(group[:10])
    if set(r['label'] for r in training + rehearsal) != set(STATIC_CLASSES):
        raise ValueError('Calibration must cover every one of the 26 classes')
    return training, validation, rehearsal


def score(model, root, records):
    values = []
    with torch.no_grad():
        for tensor, _ in torch.utils.data.DataLoader(StaticImages(root, records), batch_size=1):
            result = model(tensor)
            if result.shape != (1, len(STATIC_CLASSES)) or not torch.isfinite(result).all():
                raise ValueError('Invalid 26-class host output')
            values.append(result.detach().clone())
    logits = torch.cat(values)
    report, rows = decisions(logits, [r['label'] for r in records])
    return logits, report, [dict(path=r['path'], sha256=r['sha256'], **v) for r, v in zip(records, rows)]


def run(args):
    if args.output.exists():
        raise ValueError('Choose a new static export directory; never overwrite exports')
    common.require_source()
    common.require_python()
    common.check_space(2)
    os.environ['PATH'] = common.build_environment(1)['PATH']
    for name in ('flatc', 'vela'):
        if not (common.VENV / 'bin' / name).is_file():
            raise ValueError('Missing isolated export tool: ' + name)
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.manual_seed(2026)
    random.seed(2026)
    np.random.seed(2026)
    started = time.monotonic()
    model, checkpoint = load_checkpoint(args.checkpoint)
    config = checkpoint['configuration']
    completed_path = args.checkpoint.with_name('metrics.json')
    completed = json.loads(completed_path.read_text())
    if (checkpoint['schema'] != 3 or completed['checkpoint_sha256'] != sha256(args.checkpoint) or
            completed['final_test_evaluated'] is not False or not completed['frozen_backbone_verified']):
        raise ValueError('Use a completed static-26 training run, not a partial or final-test-tuned model')
    for path, digest in config['input_sha256'].items():
        if sha256(path) != digest:
            raise ValueError('Training dependency changed: ' + path)
    source_path, camera_path, review_path = (Path(config[k]) for k in ('source_manifest', 'camera_manifest', 'label_review'))
    source = json.loads(source_path.read_text())
    validate_manifest(source)
    source_root = Path(source['root'])
    camera_root, camera, _, _ = load_camera_data(camera_path, review_path)
    selected_path = args.checkpoint.with_name('selected.json')
    selected = json.loads(selected_path.read_text())
    for split in ('train', 'val', 'test'):
        limit = config['training_per_class'] if split == 'train' else config['eval_per_class']
        if selected['kaggle_' + split] != select_records(source, split, limit, config['seed']):
            raise ValueError('Selected Kaggle rows differ from the checkpoint training configuration')
    training, validation, rehearsal = calibration_records(selected, source, camera)
    for key, rows in selected.items():
        root = camera_root if key.startswith('camera_') else source_root
        if any(sha256(root / r['path']) != r['sha256'] for r in rows):
            raise ValueError('Selected source image changed')
    identities = {str(p): sha256(p) for p in (args.checkpoint, completed_path, selected_path, source_path,
        camera_path, review_path, Path(__file__), Path(inspector.__file__), Path(asl_selftest.__file__),
        Path('tools/asl_contract.py'), common.VENV/'bin/flatc', common.VENV/'bin/vela',
        common.WORK/'models/mv2/program_metadata.json', *Path(__file__).parent.glob('*.py'))}
    args.output.mkdir(parents=True)
    write_json(args.output/'configuration.json', dict(checkpoint=str(args.checkpoint), input_sha256=identities,
        classes=list(STATIC_CLASSES), preprocessing=checkpoint['preprocessing'], threshold=0.8, threshold_calibrated=False,
        calibration_split='TRAIN ONLY; 10/class selected Kaggle TRAIN + all reviewed camera TRAIN; no augmentation',
        camera_calibration_count=len(training), rehearsal_calibration_count=len(rehearsal),
        camera_validation_count=len(validation), kaggle_validation_count=len(selected['kaggle_val']),
        camera_unvalidated_letters=config['camera_unvalidated_letters'], final_test_evaluated=False,
        torch=str(torch.__version__), vela=importlib.metadata.version('ethos-u-vela')))
    write_json(args.output/'calibration-records.json', dict(camera_train=training, rehearsal_train=rehearsal))
    datasets = [('camera', camera_root, validation), ('kaggle', source_root, selected['kaggle_val'])]
    before = {}
    for domain, root, rows in datasets:
        logits, report, predictions = score(model, root, rows)
        before[domain] = report
        np.save(args.output/f'float-{domain}-validation-logits.npy', logits.numpy(), allow_pickle=False)
        write_json(args.output/f'float-{domain}-validation.json', dict(metrics=report, predictions=predictions))
    example = StaticImages(camera_root, training)[0][0].unsqueeze(0)

    from executorch.backends.arm.ethosu import EthosUCompileSpec, EthosUPartitioner
    from executorch.backends.arm.quantizer import EthosUQuantizer, get_symmetric_quantization_config
    from executorch.backends.cortex_m.passes.replace_quant_nodes_pass import ReplaceQuantNodesPass
    from executorch.exir import EdgeCompileConfig, ExecutorchBackendConfig, to_edge_transform_and_lower
    from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e

    intermediates = args.output.resolve()/'intermediates'
    intermediates.mkdir()
    spec = EthosUCompileSpec(common.TARGET, memory_mode='Sram_Only')
    spec.dump_intermediate_artifacts_to(str(intermediates))
    quantizer = EthosUQuantizer(spec).set_global(get_symmetric_quantization_config(is_per_channel=True))
    exported = torch.export.export(model, (example,), strict=True)
    prepared = prepare_pt2e(exported.module(check_guards=False), quantizer)
    with torch.no_grad():
        for root, rows in ((camera_root, training), (source_root, rehearsal)):
            for tensor, _ in torch.utils.data.DataLoader(StaticImages(root, rows), batch_size=1):
                prepared(tensor)
    quantized = convert_pt2e(prepared)
    after, quant_logits = {}, {}
    for domain, root, rows in datasets:
        logits, report, predictions = score(quantized, root, rows)
        after[domain], quant_logits[domain] = report, logits
        np.save(args.output/f'quantized-{domain}-validation-logits.npy', logits.numpy(), allow_pickle=False)
        write_json(args.output/f'quantized-{domain}-validation.json', dict(metrics=report, predictions=predictions,
            numerical_domain='Host PT2E simulation; not Vela/device'))
    quant_export = torch.export.export(quantized, (example,), strict=True)
    gate = asl_selftest.policy(final_output_scale(quant_export))
    torch.export.save(quant_export, args.output/'quantized.pt2')
    reloaded = torch.export.load(args.output/'quantized.pt2').module(check_guards=False)
    delta = 0.
    for domain, root, rows in datasets:
        logits, _, _ = score(reloaded, root, rows)
        delta = max(delta, float((logits - quant_logits[domain]).abs().max()))
    if delta != 0:
        raise ValueError('Quantized reload changed validation logits')
    reference = args.output/'reference'
    reference.mkdir()
    with Image.open(camera_root/training[0]['path']) as image:
        pixels = square_rgb(image).resize((128,128), Image.Resampling.BILINEAR)
        pixels.save(reference/'camera.png')
    exact = transform(128)(pixels).unsqueeze(0)
    if not torch.equal(exact, example):
        raise ValueError('Reference PNG/input mismatch')
    with torch.no_grad():
        golden = reloaded(exact)
    if not asl_selftest.compare(golden.flatten().tolist(), golden.flatten().tolist(), gate, classes=STATIC_CLASSES):
        raise ValueError('Exact host reference cannot satisfy its own numerical gate')
    np.save(reference/'input.npy', exact.numpy(), allow_pickle=False)
    (reference/'input.f32').write_bytes(exact.numpy().astype('<f4').tobytes())
    np.save(reference/'quantized-host-logits.npy', golden.numpy(), allow_pickle=False)
    write_json(reference/'reference.json', dict(source_record=training[0], classes=list(STATIC_CLASSES),
        input_sha256=sha256(reference/'input.f32'), logits_sha256=sha256(reference/'quantized-host-logits.npy'),
        host_logits=golden.flatten().tolist(), selftest_gate=gate, domain='PT2E host; device comparison pending'))
    edge = to_edge_transform_and_lower(quant_export, partitioner=[EthosUPartitioner(spec)],
        compile_config=EdgeCompileConfig(_check_ir_validity=False)).transform([ReplaceQuantNodesPass()])
    program = edge.to_executorch(config=ExecutorchBackendConfig(extract_delegate_segments=False))
    pte = args.output/'asl_ethosu85_256.pte'
    pte.write_bytes(program.buffer)
    metadata = inspector.inspect(pte, STATIC_CLASSES)
    if metadata['output_scale'] != gate['output_scale']:
        raise ValueError('Compiled output scale differs from reference graph')
    write_json(args.output/'program_metadata.json', metadata)
    memory = inspector.memory_report(metadata, json.loads((common.WORK/'models/mv2/program_metadata.json').read_text()), STATIC_CLASSES)
    write_json(args.output/'memory.json', memory)
    if any(sha256(p) != h for p,h in identities.items()):
        raise ValueError('Export dependencies changed during compilation')
    load_camera_data(camera_path, review_path)
    for key, rows in selected.items():
        root = camera_root if key.startswith('camera_') else source_root
        if any(sha256(root/r['path']) != r['sha256'] for r in rows):
            raise ValueError('Image changed during compilation')
    gates = {domain: quantization_gate(before[domain], after[domain]) for domain in before}
    manifest = dict(schema=1, status='exported_engineering_candidate_not_deployment_approved', architecture='mobilenet_v2',
        classes=list(STATIC_CLASSES), preprocessing=checkpoint['preprocessing'], target=common.TARGET, memory_mode='Sram_Only',
        executorch_revision=common.REVISION, checkpoint_sha256=sha256(args.checkpoint), pte_sha256=sha256(pte),
        pte_bytes=pte.stat().st_size, selftest_gate=gate, memory=memory,
        float_validation=before['camera'], pt2e_quantized_validation=after['camera'],
        float_kaggle_validation=before['kaggle'], pt2e_quantized_kaggle_validation=after['kaggle'],
        quantization_nonregression_by_domain=gates, quantization_nonregression_passed=all(gates.values()),
        camera_unvalidated_letters=config['camera_unvalidated_letters'], quantized_reload_max_logit_difference=delta,
        hardware_verified=False, deployment_approved=False, programming_authorized=False, final_test_evaluated=False,
        elapsed_seconds=time.monotonic()-started,
        limitations=['19 letters have no reviewed E8-camera examples; Kaggle metrics do not establish camera accuracy.',
                     'OTHER is trained from limited reviewed camera negatives only; no del/space/J/Z data.',
                     'Threshold 0.80 is provisional; PT2E simulation is not Vela/device validation.'],
        artifact_sha256={str(p.relative_to(args.output)): sha256(p) for p in args.output.rglob('*') if p.is_file()})
    write_json(args.output/'manifest.json', manifest)
    print(json.dumps(dict(pte_bytes=manifest['pte_bytes'], memory=memory, quantization_nonregression=gates), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--checkpoint', type=Path, default=Path('.work/asl/static-train-v1/best.pth'))
    parser.add_argument('--output', type=Path, default=Path('.work/asl/static-export-v1'))
    run(parser.parse_args())


if __name__ == '__main__':
    main()
