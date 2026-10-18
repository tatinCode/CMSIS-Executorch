"""Training-only int8 calibration and isolated ASL U85 export. Never flashes.

Validation evaluates PT2E simulation, not Vela/device numerical equivalence.
The selected checkpoint is an engineering candidate, not a demo-ready model.
"""

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

from .adapt import AdaptImages, decisions, load_camera_data
from .camera_data import CAMERA_CLASSES, write_json
from .data import sha256
from .model import contract, load_checkpoint, square_rgb, transform
from .train import validate_manifest
from tools import inference as common
from tools import inspect_asl_pte as inspector
from tools import asl_selftest


def final_output_scale(exported):
    outputs = [n for n in exported.graph.nodes if n.op == "output"]
    if len(outputs) != 1 or len(outputs[0].args) != 1 or len(outputs[0].args[0]) != 1:
        raise ValueError("Expected one ASL graph output")
    node = outputs[0].args[0][0]
    if (str(node.target) != "quantized_decomposed.dequantize_per_tensor.default" or
            len(node.args) != 6 or node.args[3:6] != (-128, 127, torch.int8)):
        raise ValueError("ASL output must be an int8 per-tensor dequantization")
    return asl_selftest.output_scale(node.args[1])


def validate_candidate(checkpoint, completed, checkpoint_hash, camera_hash, review_hash):
    if (checkpoint.get("schema") != 2 or checkpoint.get("classes") != list(CAMERA_CLASSES) or
            checkpoint.get("preprocessing") != contract(128) or
            completed.get("checkpoint_sha256") != checkpoint_hash or
            completed.get("final_test_evaluated") is not False or
            checkpoint.get("configuration", {}).get("camera_manifest_sha256") != camera_hash or
            checkpoint.get("configuration", {}).get("label_review_sha256") != review_hash):
        raise ValueError("Use a completed seven-class checkpoint bound to this reviewed camera split")


def calibration_records(records, selected, source):
    """Use exactly the reviewed camera TRAIN and recorded rehearsal TRAIN rows."""
    camera = [r for r in records if r["split"] == "train"]
    validation = [r for r in records if r["split"] == "val"]
    if selected.get("camera_train") != camera or selected.get("camera_val") != validation:
        raise ValueError("Checkpoint selection differs from the current reviewed camera audit")
    known = {r["path"]: r for r in source["records"]}
    rehearsal = selected.get("rehearsal_train", [])
    if (not rehearsal or len({r["path"] for r in rehearsal}) != len(rehearsal) or
            any(r.get("split") != "train" or known.get(r["path"]) != r for r in rehearsal)):
        raise ValueError("Calibration rehearsal must be unchanged original TRAIN rows, never validation/test")
    if set(r["label"] for r in camera) != set(CAMERA_CLASSES):
        raise ValueError("Calibration needs all seven camera training classes")
    return camera, validation, rehearsal


def score(model, dataset, records, threshold):
    outputs = []
    with torch.no_grad():
        for index in range(len(dataset)):
            tensor, _ = dataset[index]
            result = model(tensor.unsqueeze(0))
            if result.shape != (1, 7) or not torch.isfinite(result).all():
                raise ValueError("Invalid ASL logits during host evaluation")
            outputs.append(result.detach().clone())
    logits = torch.cat(outputs)
    report, rows = decisions(logits, [r["label"] for r in records], CAMERA_CLASSES, threshold)
    return logits, report, [dict(path=r["path"], sha256=r["sha256"], **row) for r, row in zip(records, rows)]


def quantization_gate(before, after):
    # Predeclared engineering nonregression gate, not a claim of good accuracy.
    return (after["correct_letter_count"] >= before["correct_letter_count"] and
            after["wrong_letter_count"] <= before["wrong_letter_count"] and
            after["negative_false_letter_count"] <= before["negative_false_letter_count"])


def run(args):
    if args.output.exists():
        raise ValueError("Choose a new ASL export output directory; existing outputs are retained")
    if args.threshold != 0.8:
        raise ValueError("This engineering export keeps the existing provisional 0.80 threshold")
    common.require_source()
    common.require_python()
    common.check_space(2)
    # Invoking the venv Python directly does not activate its command PATH.
    # Use the already-installed isolated flatc/Vela, never global alternatives.
    os.environ["PATH"] = common.build_environment(1)["PATH"]
    for name in ("flatc", "vela"):
        if not (common.VENV / "bin" / name).is_file():
            raise ValueError(f"Missing isolated {name}; run the existing tooling setup explicitly")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.manual_seed(2026)
    random.seed(2026)
    np.random.seed(2026)
    started = time.monotonic()
    root, records, _, _ = load_camera_data(args.camera_manifest, args.label_review)
    model, checkpoint = load_checkpoint(args.checkpoint)
    completed_path = args.checkpoint.with_name("metrics.json")
    completed = json.loads(completed_path.read_text())
    validate_candidate(checkpoint, completed, sha256(args.checkpoint),
                       sha256(args.camera_manifest), sha256(args.label_review))
    source_path = Path(checkpoint["configuration"]["source_manifest"])
    if sha256(source_path) != checkpoint["configuration"]["source_manifest_sha256"]:
        raise ValueError("Original rehearsal dataset manifest changed")
    source = json.loads(source_path.read_text())
    validate_manifest(source)
    selected_path = args.checkpoint.with_name("selected.json")
    selected = json.loads(selected_path.read_text())
    training, validation, rehearsal = calibration_records(records, selected, source)
    source_root = Path(source["root"]).resolve(strict=True)
    for row in rehearsal:
        if sha256(source_root / row["path"]) != row["sha256"]:
            raise ValueError("Original TRAIN calibration image changed")
    identities = {str(p): sha256(p) for p in (args.checkpoint, completed_path, selected_path,
                  args.camera_manifest, args.label_review, source_path, Path(__file__), Path(inspector.__file__),
                  common.VENV / "bin/flatc", common.VENV / "bin/vela",
                  common.WORK / "models/mv2/program_metadata.json", Path(asl_selftest.__file__))}
    identities.update({str(p): sha256(p) for p in Path(__file__).parent.glob("*.py")})
    args.output.mkdir(parents=True)
    write_json(args.output / "configuration.json", dict(checkpoint=str(args.checkpoint), input_sha256=identities,
               classes=list(CAMERA_CLASSES), preprocessing=contract(128), threshold=args.threshold,
               threshold_calibrated=False, camera_calibration_count=len(training),
               rehearsal_calibration_count=len(rehearsal), validation_count=len(validation),
               calibration_split="TRAIN only; base images; no augmentation; no validation observers",
               gate="correct letters >= float; wrong letters <= float; negative false letters <= float",
               target=common.TARGET, memory_mode="Sram_Only", threads=1, final_test_evaluated=False,
               torch=str(torch.__version__), torchao=importlib.metadata.version("torchao"),
               vela=importlib.metadata.version("ethos-u-vela")))
    write_json(args.output / "calibration-records.json", dict(camera_train=training, rehearsal_train=rehearsal))
    train_images = AdaptImages(root, training)
    validation_images = AdaptImages(root, validation)
    example = train_images[0][0].unsqueeze(0)
    float_logits, float_metrics, float_rows = score(model, validation_images, validation, args.threshold)
    write_json(args.output / "float-validation.json", dict(metrics=float_metrics, predictions=float_rows))

    from executorch.backends.arm.ethosu import EthosUCompileSpec, EthosUPartitioner
    from executorch.backends.arm.quantizer import EthosUQuantizer, get_symmetric_quantization_config
    from executorch.backends.cortex_m.passes.replace_quant_nodes_pass import ReplaceQuantNodesPass
    from executorch.devtools.backend_debug import get_delegation_info
    from executorch.exir import EdgeCompileConfig, ExecutorchBackendConfig, to_edge_transform_and_lower
    from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e

    intermediates = args.output.resolve() / "intermediates"
    intermediates.mkdir()
    spec = EthosUCompileSpec(common.TARGET, memory_mode="Sram_Only")
    spec.dump_intermediate_artifacts_to(str(intermediates))
    quantizer = EthosUQuantizer(spec).set_global(get_symmetric_quantization_config(is_per_channel=True))
    exported = torch.export.export(model, (example,), strict=True)
    # Torch 2.10's default module() inserts a _guards_fn call_module, which
    # this pinned Arm quantizer cannot consume. Retain the fixed-shape checks
    # as export pre-hooks instead; independently verify the final PTE shape.
    prepared = prepare_pt2e(exported.module(check_guards=False), quantizer)
    with torch.no_grad():
        for name, dataset in (("camera TRAIN", train_images),
                              ("original TRAIN", AdaptImages(source_root, rehearsal))):
            for index in range(len(dataset)):
                tensor, _ = dataset[index]
                prepared(tensor.unsqueeze(0))
            print(f"Calibrated {name}: {len(dataset)}", flush=True)
    quantized = convert_pt2e(prepared)
    q_logits, q_metrics, q_rows = score(quantized, validation_images, validation, args.threshold)
    write_json(args.output / "quantized-validation.json", dict(metrics=q_metrics, predictions=q_rows,
               numerical_domain="Host PT2E quantization simulation; NOT Vela/device execution"))
    np.save(args.output / "float-validation-logits.npy", float_logits.numpy(), allow_pickle=False)
    np.save(args.output / "quantized-validation-logits.npy", q_logits.numpy(), allow_pickle=False)
    quant_export = torch.export.export(quantized, (example,), strict=True)
    gate = asl_selftest.policy(final_output_scale(quant_export))
    torch.export.save(quant_export, args.output / "quantized.pt2")
    reloaded = torch.export.load(args.output / "quantized.pt2").module(check_guards=False)
    reloaded_logits, _, _ = score(reloaded, validation_images, validation, args.threshold)
    reload_delta = float((q_logits - reloaded_logits).abs().max())
    if reload_delta != 0:
        raise ValueError("Reloaded quantized graph changed validation logits")

    reference = args.output / "reference"
    reference.mkdir()
    with Image.open(root / training[0]["path"]) as image:
        pixels = square_rgb(image).resize((128, 128), Image.Resampling.BILINEAR)
        pixels.save(reference / "camera.png")
    exact_input = transform(128)(pixels).unsqueeze(0)
    if not torch.equal(example, exact_input):
        raise ValueError("Reference camera PNG does not reproduce exact model input")
    with torch.no_grad():
        golden = reloaded(exact_input)
    np.save(reference / "input.npy", exact_input.numpy(), allow_pickle=False)
    (reference / "input.f32").write_bytes(exact_input.numpy().astype("<f4").tobytes())
    np.save(reference / "quantized-host-logits.npy", golden.numpy(), allow_pickle=False)
    write_json(reference / "reference.json", dict(source_record=training[0], input_sha256=sha256(reference / "input.f32"),
               logits_sha256=sha256(reference / "quantized-host-logits.npy"),
                host_logits=golden.flatten().tolist(), selftest_gate=gate,
                domain="PT2E host simulation; device comparison pending"))

    edge = to_edge_transform_and_lower(quant_export, partitioner=[EthosUPartitioner(spec)],
                                      compile_config=EdgeCompileConfig(_check_ir_validity=False))
    edge = edge.transform([ReplaceQuantNodesPass()])
    delegation = get_delegation_info(edge.exported_program().graph_module)
    (intermediates / "delegation_info.txt").write_text(delegation.get_summary() + "\n" +
        delegation.get_operator_delegation_dataframe().to_string() + "\n")
    program = edge.to_executorch(config=ExecutorchBackendConfig(extract_delegate_segments=False))
    pte = args.output / "asl_ethosu85_256.pte"
    pte.write_bytes(program.buffer)
    metadata = inspector.inspect(pte)
    if metadata["output_scale"] != gate["output_scale"]:
        raise ValueError("Compiled PTE output scale differs from the exported graph")
    write_json(args.output / "program_metadata.json", metadata)
    baseline_path = common.WORK / "models/mv2/program_metadata.json"
    memory = inspector.memory_report(metadata, json.loads(baseline_path.read_text()))
    write_json(args.output / "memory.json", memory)
    # Revalidate all bound inputs and source data after compilation.
    if any(sha256(p) != digest for p, digest in identities.items()):
        raise ValueError("Export input changed during quantization/lowering")
    load_camera_data(args.camera_manifest, args.label_review)
    if any(sha256(source_root / r["path"]) != r["sha256"] for r in rehearsal):
        raise ValueError("Calibration images changed during compilation")
    artifacts = {str(p.relative_to(args.output)): sha256(p) for p in args.output.rglob("*") if p.is_file()}
    manifest = dict(schema=1, status="exported_engineering_candidate_not_deployment_approved",
        architecture="mobilenet_v2", checkpoint_sha256=sha256(args.checkpoint),
        classes=list(CAMERA_CLASSES), preprocessing=contract(128), target=common.TARGET,
        memory_mode="Sram_Only", executorch_revision=common.REVISION,
        selftest_gate=gate,
        pte_sha256=sha256(pte), pte_bytes=pte.stat().st_size, artifact_sha256=artifacts,
        float_validation=float_metrics, pt2e_quantized_validation=q_metrics,
        quantization_nonregression_passed=quantization_gate(float_metrics, q_metrics),
        quantized_reload_max_logit_difference=reload_delta, memory=memory,
        elapsed_seconds=time.monotonic() - started, hardware_verified=False, deployment_approved=False,
        programming_authorized=False, final_test_evaluated=False,
        limitations=["Small development session, not a final accuracy benchmark.",
                     "PT2E simulation is not compiled Vela or device validation.",
                     "OTHER/NO_HAND rejection is not guaranteed on unseen scenes."])
    write_json(args.output / "manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("pte_bytes", "quantization_nonregression_passed", "memory")}, indent=2))
    print("Export completed. No board accessed; device comparison and live firmware are still required.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkpoint", type=Path, default=Path(".work/asl/adapt-v1/best.pth"))
    parser.add_argument("--camera-manifest", type=Path, default=Path(".work/asl/camera-audit-v1/manifest.json"))
    parser.add_argument("--label-review", type=Path, default=Path(".work/asl/camera-audit-v1/label-review.json"))
    parser.add_argument("--output", type=Path, default=Path(".work/asl/export-v1"))
    parser.add_argument("--threshold", type=float, default=0.8)
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
