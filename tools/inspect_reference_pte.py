#!/usr/bin/env python3
"""Validate the exported reference model's interface and record memory planning."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


def validate_methods(methods, model):
    if model not in ("smoke", "mv2"):
        raise ValueError("Unknown reference model")
    expected_input = [1, 16] if model == "smoke" else [1, 3, 224, 224]
    expected_output = [1, 16] if model == "smoke" else [1, 1000]
    if not isinstance(methods, list) or len(methods) != 1 or not isinstance(methods[0], dict):
        raise ValueError("Reference model requires exactly one method")
    forward = next((method for method in methods if method.get("name") == "forward"), None)
    if forward is None:
        raise ValueError("Missing forward method")
    if any(not isinstance(forward.get(key), list) or len(forward[key]) != 1 for key in ("inputs", "outputs")):
        raise ValueError("Reference model requires exactly one tensor input and output")
    for tensor, shape in ((forward["inputs"][0], expected_input), (forward["outputs"][0], expected_output)):
        if not isinstance(tensor, dict) or tensor.get("dtype") != "FLOAT" or tensor.get("shape") != shape:
            raise ValueError(f"Unexpected model tensor contract: {tensor}; expected float32 {shape}")
        if tensor.get("dim_order") != list(range(len(shape))):
            raise ValueError("Tensor is not in the expected contiguous logical order")
    if not isinstance(forward.get("delegates"), list) or "EthosUBackend" not in forward["delegates"]:
        raise ValueError("The forward method has no Ethos-U delegate; refusing a CPU-only export")
    sizes = forward.get("planned_buffer_bytes")
    if not isinstance(sizes, list) or any(type(size) is not int or size < 0 for size in sizes):
        raise ValueError("Invalid planned-buffer sizes")


def inspect_vela_payload(data):
    """Decode the pinned VelaBinStream format, never guess scratch from arena size."""
    expected = ("vela_bin_stream", "cmd_data", "weight_data", "scratch_size", "inputs", "outputs", "vela_end_stream")
    blocks = {}
    offset = 0
    for name in expected:
        if len(data) - offset < 32:
            raise ValueError("Truncated Vela block header")
        encoded = name.encode().ljust(16, b"\0")
        if data[offset:offset + 16] != encoded or data[offset + 20:offset + 32] != bytes(12):
            raise ValueError(f"Unexpected Vela block; expected {name}")
        size = struct.unpack_from("<I", data, offset + 16)[0]
        start = offset + 32
        end = start + ((size + 15) // 16) * 16
        if end > len(data) or any(data[start + size:end]):
            raise ValueError("Truncated Vela block or invalid padding")
        blocks[name] = data[start:start + size]
        offset = end
    if offset != len(data) or blocks["vela_bin_stream"] or blocks["vela_end_stream"]:
        raise ValueError("Invalid Vela stream boundaries")
    if not blocks["cmd_data"].startswith(b"COP1") or len(blocks["scratch_size"]) != 4:
        raise ValueError("Invalid Vela command/scratch block")
    scratch = struct.unpack("<I", blocks["scratch_size"])[0]
    if scratch == 0:
        raise ValueError("Reference Ethos-U delegate requires nonempty scratch")
    for name in ("inputs", "outputs"):
        io = blocks[name]
        if len(io) < 4:
            raise ValueError("Truncated Vela I/O count")
        count = struct.unpack_from("<i", io)[0]
        if count <= 0 or len(io) != 4 + count * 36:
            raise ValueError("Invalid Vela I/O descriptors")
        for index in range(count):
            *shape, elem_size, address, region = struct.unpack_from("<9i", io, 4 + index * 36)
            elements = 1
            for dim in shape:
                if dim <= 0:
                    raise ValueError("Invalid Vela I/O shape")
                elements *= dim
            if elem_size not in (1, 2, 4) or region != 1 or address < 0 or address + elements * elem_size > scratch:
                raise ValueError("Vela I/O does not fit the supported scratch region")
    return {"scratch_bytes": scratch, "payload_sha256": hashlib.sha256(data).hexdigest()}


def inspect(pte, model):
    from executorch.exir._serialize._program import deserialize_pte_binary
    from executorch.exir.schema import DataLocation, Tensor

    binary = Path(pte).read_bytes()
    program = deserialize_pte_binary(binary).program
    methods = []
    delegate_memory = []
    for plan in program.execution_plan:
        for index, delegate in enumerate(plan.delegates):
            if delegate.id != "EthosUBackend":
                raise ValueError(f"Unsupported reference delegate: {delegate.id}")
            reference = delegate.processed
            if reference.location != DataLocation.INLINE or not 0 <= reference.index < len(program.backend_delegate_data):
                raise ValueError("Unsupported or invalid delegate data reference")
            memory = inspect_vela_payload(bytes(program.backend_delegate_data[reference.index].data))
            delegate_memory.append({"method": plan.name, "delegate_index": index, **memory})
        def describe(index):
            value = plan.values[index].val
            if not isinstance(value, Tensor):
                raise ValueError("Reference input/output is not a tensor")
            return {"shape": value.sizes, "dtype": value.scalar_type.name, "dim_order": value.dim_order}

        methods.append({
            "name": plan.name,
            "inputs": [describe(index) for index in plan.inputs],
            "outputs": [describe(index) for index in plan.outputs],
            "delegates": [delegate.id for delegate in plan.delegates],
            "cpu_operators": [{"name": op.name, "overload": op.overload} for op in plan.operators],
            # Buffer 0 is the constant-memory sentinel, not an arena to allocate.
            "planned_buffer_bytes": plan.non_const_buffer_sizes[1:],
        })
    validate_methods(methods, model)
    return {"schema_version": program.version, "methods": methods, "delegate_memory": delegate_memory,
            "pte_sha256": hashlib.sha256(binary).hexdigest(), "pte_bytes": len(binary),
            "hardware_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pte", type=Path)
    parser.add_argument("--model", choices=("smoke", "mv2"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.pte, args.model)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
