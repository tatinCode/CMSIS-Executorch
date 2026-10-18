#!/usr/bin/env python3
"""Fail-closed budget estimate for explicit E8-HP stored-image profiles. Never flashes."""

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import inference as common
from tools.inspect_reference_pte import validate_methods
from tools.firmware_layouts import CURRENT, LAYOUTS, select_layout


ALIGNMENT = 32
INPUT_BYTES = 1 * 3 * 224 * 224 * 4
OUTPUT_BYTES = 1 * 1000 * 4
LIMITS = CURRENT.limits
BUDGET_NAMES = (
    "scratch_bytes", "temporary_overhead_bytes", "arena_bytes", "sram_bytes", "mram_bytes",
    "firmware_mram_bytes", "other_mram_bytes", "method_pool_bytes", "other_sram_bytes",
    "other_dtcm_bytes", "heap_bytes", "stack_bytes", "dtcm_bytes",
)
POSITIVE_BUDGETS = {"scratch_bytes", "arena_bytes", "firmware_mram_bytes", "method_pool_bytes",
                    "heap_bytes", "stack_bytes", *LIMITS}
PREPROCESSING = {
    "exif_transpose": True, "color": "RGB", "resize_short_edge": 256,
    "resize": "bilinear, antialias=True", "center_crop": [224, 224],
    "scale": "uint8 / 255", "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
    "layout": "NCHW", "shape": [1, 3, 224, 224], "dtype": "float32", "raw_endianness": "little",
}


class PreflightError(ValueError):
    """Artifacts or declared memory reservations cannot be accepted."""


sha256 = common.sha256


def align_up(value, alignment=ALIGNMENT):
    if type(value) is not int or value < 0 or type(alignment) is not int or alignment <= 0 or alignment & (alignment - 1):
        raise PreflightError("sizes must be non-negative integers; alignment must be a positive power of two")
    return ((value + alignment - 1) // alignment) * alignment


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise PreflightError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise PreflightError(f"expected a JSON object: {path}")
    return value


def inspect_pte(pte):
    """Always inspect the actual PTE using this project's pinned, isolated tools."""
    common.require_source()
    common.require_python()
    common.prepare_work()
    with tempfile.TemporaryDirectory(prefix="preflight-", dir=common.WORK / "tmp") as directory:
        output = Path(directory) / "inspection.json"
        result = subprocess.run([
            str(common.PYTHON), str(common.ROOT / "tools/inspect_reference_pte.py"), str(pte),
            "--model", "mv2", "--output", str(output),
        ], env=common.build_environment(), text=True, capture_output=True)
        if result.returncode:
            raise PreflightError("actual PTE inspection failed:\n" + result.stderr[-4000:])
        return read_json(output)


def read_float_npy(path, expected_shape):
    """Read only bounded, C-order, little-endian float32 NPY; never load pickle."""
    with Path(path).open("rb") as stream:
        prefix = stream.read(8)
        if prefix[:6] != b"\x93NUMPY" or prefix[6:] not in (b"\x01\x00", b"\x02\x00", b"\x03\x00"):
            raise PreflightError(f"invalid NPY file: {path}")
        length_bytes = 2 if prefix[6] == 1 else 4
        encoded = stream.read(length_bytes)
        if len(encoded) != length_bytes:
            raise PreflightError("truncated NPY header length")
        length = int.from_bytes(encoded, "little")
        if not 0 < length <= 65536:
            raise PreflightError("invalid NPY header length")
        header = stream.read(length)
        if len(header) != length:
            raise PreflightError("truncated NPY header")
        try:
            info = ast.literal_eval(header.decode("utf-8" if prefix[6] == 3 else "latin1").strip())
        except (ValueError, SyntaxError, UnicodeError) as error:
            raise PreflightError("invalid NPY header") from error
        if (not isinstance(info, dict) or set(info) != {"descr", "fortran_order", "shape"}
                or info["descr"] != "<f4" or info["fortran_order"] is not False
                or info["shape"] != expected_shape
                or any(type(dim) is not int for dim in info["shape"])):
            raise PreflightError(f"expected C-order little-endian float32 {expected_shape}: {path}")
        count = math.prod(expected_shape) * 4
        data = stream.read(count + 1)
        if len(data) != count or any(not math.isfinite(value) for value, in struct.iter_unpack("<f", data)):
            raise PreflightError(f"invalid length or non-finite float data: {path}")
        return data


def check_artifacts(model_dir, image):
    model_dir = Path(model_dir)
    manifest = read_json(model_dir / "manifest.json")
    metadata_path = model_dir / "program_metadata.json"
    pte = model_dir / "mv2_ethosu85_256.pte"
    reference_dir = model_dir / "reference"
    reference = read_json(reference_dir / "reference.json")
    # Legacy exports remain inspectable, but cannot receive a successful fit:
    # newly generated references record identities BEFORE model compilation.
    binding_errors = []
    for label, stored, path in (
        ("export/reference", manifest.get("reference_sha256"), reference_dir / "reference.json"),
        ("reference/input.npy", reference.get("input_npy_sha256"), reference_dir / "input.npy"),
        ("reference/host_logits.npy", reference.get("host_logits_sha256"), reference_dir / "host_logits.npy"),
    ):
        if stored is None:
            binding_errors.append(f"missing {label} hash binding; regenerate reference/model export before accepting a fit")
        elif stored != sha256(path):
            raise PreflightError(f"{label} hash differs from the recorded artifact")
    if (manifest.get("model") != "mv2" or manifest.get("memory_mode") != "Sram_Only"
            or manifest.get("target") != common.TARGET or manifest.get("executorch_revision") != common.REVISION):
        raise PreflightError("expected pinned mv2 / ethos-u85-256 / Sram_Only artifacts")
    if type(manifest.get("pte_bytes")) is not int or not 0 < manifest["pte_bytes"] == pte.stat().st_size:
        raise PreflightError("manifest model size does not match the nonempty .pte")
    model_hash = sha256(pte)
    if manifest.get("pte_sha256") != model_hash or manifest.get("metadata_sha256") != sha256(metadata_path):
        raise PreflightError("model or metadata hash differs from its manifest")
    metadata = read_json(metadata_path)
    try:
        validate_methods(metadata.get("methods"), "mv2")
    except ValueError as error:
        raise PreflightError(str(error)) from error
    actual = inspect_pte(pte)
    if (actual.get("pte_sha256") != model_hash or actual.get("pte_bytes") != manifest["pte_bytes"]
            or actual.get("schema_version") != metadata.get("schema_version")
            or actual.get("methods") != metadata["methods"]):
        raise PreflightError("cached metadata does not match inspection of the actual .pte")
    method = actual["methods"][0]
    delegates = method["delegates"]
    memory = actual.get("delegate_memory")
    if not delegates or any(delegate != "EthosUBackend" for delegate in delegates):
        raise PreflightError("unsupported reference delegates")
    if not isinstance(memory, list) or len(memory) != len(delegates):
        raise PreflightError("missing actual Ethos-U scratch requirements")
    for index, item in enumerate(memory):
        if (not isinstance(item, dict) or item.get("method") != "forward" or item.get("delegate_index") != index
                or type(item.get("scratch_bytes")) is not int or item["scratch_bytes"] <= 0):
            raise PreflightError("invalid actual Ethos-U scratch requirements")
    if reference.get("preprocessing") != PREPROCESSING or reference.get("weights") != "MobileNet_V2_Weights.IMAGENET1K_V2":
        raise PreflightError("unexpected preprocessing/weight contract")
    if sha256(image) != reference.get("source_sha256"):
        raise PreflightError("source image differs from the host reference")
    input_path = reference_dir / "input.f32"
    if reference.get("input_bytes") != INPUT_BYTES or input_path.stat().st_size != INPUT_BYTES:
        raise PreflightError(f"expected {INPUT_BYTES} bytes of float32 input")
    raw = input_path.read_bytes()
    input_hash = hashlib.sha256(raw).hexdigest()
    if input_hash != reference.get("input_sha256"):
        raise PreflightError("reference input hash mismatch")
    if read_float_npy(reference_dir / "input.npy", (1, 3, 224, 224)) != raw:
        raise PreflightError("export input.npy differs from firmware input.f32")
    logits_path = reference_dir / "host_logits.npy"
    logits = [value for value, in struct.iter_unpack("<f", read_float_npy(logits_path, (1, 1000)))]
    top = reference.get("host_top5")
    if not isinstance(top, list) or len(top) != 5 or any(not isinstance(item, dict) for item in top):
        raise PreflightError("missing host top-5 reference")
    indices = [item.get("index") for item in top]
    if any(type(index) is not int or not 0 <= index < 1000 for index in indices) or len(set(indices)) != 5:
        raise PreflightError("invalid host top-5 indices")
    threshold = sorted(logits, reverse=True)[4]
    for item in top:
        if (not isinstance(item.get("label"), str) or not item["label"]
                or type(item.get("logit")) not in (int, float) or item["logit"] != logits[item["index"]]
                or item["logit"] < threshold):
            raise PreflightError("host logits disagree with the top-5 reference")
    if [item["logit"] for item in top] != sorted((item["logit"] for item in top), reverse=True):
        raise PreflightError("host top-5 reference is not sorted")
    # Sum is a conservative bound across delegates, not an assumption that the
    # runtime can always reuse a single scratch allocation between instructions.
    return {
        "model_bytes": pte.stat().st_size, "input_bytes": INPUT_BYTES, "output_bytes": OUTPUT_BYTES,
        "planned_buffers": method["planned_buffer_bytes"],
        "arena_required_bytes": sum(align_up(size) for size in method["planned_buffer_bytes"]),
        "scratch_required_bytes": sum(align_up(item["scratch_bytes"]) for item in memory),
        "delegate_memory": memory,
        "reference_binding_errors": binding_errors,
        "hashes": {"model": model_hash, "metadata": sha256(metadata_path),
                   "reference": sha256(reference_dir / "reference.json"), "source_image": sha256(image),
                   "input_f32": input_hash, "input_npy": sha256(reference_dir / "input.npy"),
                   "host_logits": sha256(logits_path)},
    }


def validate_budgets(budgets, layout=CURRENT.name):
    profile = select_layout(layout)
    if set(budgets) != set(BUDGET_NAMES):
        raise PreflightError("explicit budgets required: " + ", ".join(BUDGET_NAMES))
    for name in BUDGET_NAMES:
        value = budgets[name]
        if type(value) is not int or value < 0 or (name in POSITIVE_BUDGETS and value == 0):
            raise PreflightError(f"explicit {'positive' if name in POSITIVE_BUDGETS else 'non-negative'} integer {name} is required")
    for name, maximum in profile.limits.items():
        if budgets[name] > maximum:
            raise PreflightError(f"{name} exceeds the {profile.name} layout limit ({maximum}); preflight cannot authorize a new layout")


def preflight(model_dir, budgets, image, layout=CURRENT.name):
    validate_budgets(budgets, layout)
    profile = select_layout(layout)
    requirements = check_artifacts(model_dir, image)
    reserved = {name: align_up(value) for name, value in budgets.items() if name not in LIMITS}
    errors = list(requirements["reference_binding_errors"])
    if requirements["arena_required_bytes"] > budgets["arena_bytes"]:
        errors.append("planned buffers exceed the arena reservation")
    scratch_needed = requirements["scratch_required_bytes"] + reserved["temporary_overhead_bytes"]
    if scratch_needed > budgets["scratch_bytes"]:
        errors.append("actual delegate scratch plus temporary overhead exceeds the scratch/temporary-pool reservation")
    # Fixed, conservative storage policy: float input AND golden logits in MRAM,
    # separate I/O buffers in SRAM, plus planned and temporary pools. This may
    # overcount I/O already planned by ET; it must never silently undercount it.
    requirements["mram_required_bytes"] = sum(align_up(requirements[name]) for name in ("model_bytes", "input_bytes", "output_bytes")) + reserved["firmware_mram_bytes"] + reserved["other_mram_bytes"]
    requirements["sram_reserved_bytes"] = INPUT_BYTES + align_up(OUTPUT_BYTES) + reserved["arena_bytes"] + reserved["scratch_bytes"] + reserved["other_sram_bytes"]
    requirements["dtcm_reserved_bytes"] = sum(reserved[name] for name in ("method_pool_bytes", "other_dtcm_bytes", "heap_bytes", "stack_bytes"))
    for needed, capacity, label in (("mram_required_bytes", "mram_bytes", "MRAM"),
                                     ("sram_reserved_bytes", "sram_bytes", "SRAM"),
                                     ("dtcm_reserved_bytes", "dtcm_bytes", "DTCM")):
        if requirements[needed] > budgets[capacity]:
            errors.append(f"{label} reservations require {requirements[needed]} bytes; only {budgets[capacity]} declared")
    return {
        "format": 1, "status": "blocked" if errors else "fits_declared_budgets",
        "fits": not errors, "errors": errors, "alignment": ALIGNMENT,
        "layout": profile.report(),
        "requirements": requirements, "budgets": budgets, "aligned_reservations": reserved,
        "firmware_link_verified": False, "hardware_verified": False,
        "limitations": ["CPU/firmware/other-memory reservations are caller estimates, not measured requirements.",
                        "MRAM embeds float input and golden logits; SRAM reserves separate I/O buffers conservatively.",
                        "Temporary overhead must be declared in addition to the decoded delegate scratch.",
                        "Fresh hashes identify reference files; they do not independently reproduce host inference or calibration."],
    }


def positive_bytes(value):
    try:
        parsed = int(value)
    except (ValueError, TypeError) as error:
        raise PreflightError("memory sizes must be explicit decimal integers") from error
    if parsed < 0:
        raise PreflightError("memory sizes must be non-negative")
    return parsed


def write_report(path, report):
    # Same-filesystem replace prevents readers observing a partial JSON report.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--image", type=Path, required=True, help="Original local image used for the host reference")
    parser.add_argument("--output", type=Path, required=True, help="Dedicated preflight JSON report, never an input artifact")
    parser.add_argument("--layout", choices=LAYOUTS, default=CURRENT.name,
                        help="Opt-in profile; hp-only-large reclaims HE/user MRAM and does not authorize flashing")
    for name in BUDGET_NAMES:
        # Missing/malformed budgets are validated AFTER invalidating any old fit.
        parser.add_argument("--" + name.replace("_", "-"))
    args = parser.parse_args(argv)
    output = args.output.expanduser().absolute()
    model_dir = args.model_dir.expanduser().resolve()
    image = args.image.expanduser().resolve()
    protected = {image, model_dir / "manifest.json", model_dir / "program_metadata.json",
                 model_dir / "mv2_ethosu85_256.pte", Path(__file__).resolve()}
    if (output.is_symlink() or output.resolve() in protected or output.resolve().is_relative_to(model_dir / "reference")
            or output.suffix != ".json"):
        parser.error("--output must be a dedicated JSON report, not a source/reference artifact or symlink")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        budgets = {name: positive_bytes(getattr(args, name)) for name in BUDGET_NAMES}
        result = preflight(model_dir, budgets, image, args.layout)
    except (OSError, ValueError, KeyError, TypeError, common.SetupError, subprocess.SubprocessError) as error:
        result = {"format": 1, "status": "blocked", "fits": False, "errors": [str(error)],
                  "firmware_link_verified": False, "hardware_verified": False}
    try:
        write_report(output, result)
    except OSError as error:
        print(f"ERROR: cannot publish preflight report: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["fits"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
