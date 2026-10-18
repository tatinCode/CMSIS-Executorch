"""Inspect an ASL U85 program without relaxing the ImageNet baseline's checks."""

import hashlib
from pathlib import Path

from tools.inspect_reference_pte import inspect_vela_payload
from tools.asl_selftest import output_scale
from tools.asl_contract import LEGACY_CLASSES, validate_classes


CPU_OPERATORS = {
    ("cortex_m::quantize_per_tensor", "out"),
    ("cortex_m::dequantize_per_tensor", "out"),
}


def validate_methods(methods, classes=LEGACY_CLASSES):
    classes = validate_classes(classes)
    if not isinstance(methods, list) or len(methods) != 1:
        raise ValueError("ASL requires exactly one forward method")
    method = methods[0]
    if method.get("name") != "forward" or method.get("delegates") != ["EthosUBackend"]:
        raise ValueError("ASL requires one Ethos-U delegate")
    for name, shape in (("inputs", [1, 3, 128, 128]), ("outputs", [1, len(classes)])):
        values = method.get(name)
        if (not isinstance(values, list) or len(values) != 1 or
                values[0] != dict(shape=shape, dtype="FLOAT", dim_order=list(range(len(shape))))):
            raise ValueError(f"ASL {name} must be contiguous float32 {shape}")
    operators = method.get("cpu_operators")
    if (not isinstance(operators, list) or
            {(op.get("name"), op.get("overload")) for op in operators} != CPU_OPERATORS):
        raise ValueError("ASL may use CPU only for the existing quantize/dequantize boundary kernels")
    sizes = method.get("planned_buffer_bytes")
    if (not isinstance(sizes, list) or not 1 <= len(sizes) <= 4 or
            any(type(size) is not int or size <= 0 for size in sizes)):
        raise ValueError("Invalid ASL planned-buffer sizes")


def inspect(pte, classes=LEGACY_CLASSES):
    classes = validate_classes(classes)
    from executorch.exir._serialize._program import deserialize_pte_binary
    from executorch.exir.schema import DataLocation, Tensor, KernelCall, Double, Int, ScalarType

    binary = Path(pte).read_bytes()
    program = deserialize_pte_binary(binary).program
    methods, delegate_memory = [], []
    for plan in program.execution_plan:
        for index, delegate in enumerate(plan.delegates):
            reference = delegate.processed
            if (delegate.id != "EthosUBackend" or reference.location != DataLocation.INLINE or
                    not 0 <= reference.index < len(program.backend_delegate_data)):
                raise ValueError("Unsupported ASL delegate or external payload")
            memory = inspect_vela_payload(bytes(program.backend_delegate_data[reference.index].data))
            delegate_memory.append(dict(method=plan.name, delegate_index=index, **memory))

        def describe(index):
            value = plan.values[index].val
            if not isinstance(value, Tensor):
                raise ValueError("ASL input/output is not a tensor")
            return dict(shape=value.sizes, dtype=value.scalar_type.name, dim_order=value.dim_order)

        methods.append(dict(name=plan.name, inputs=[describe(i) for i in plan.inputs],
                            outputs=[describe(i) for i in plan.outputs],
                            delegates=[d.id for d in plan.delegates],
                            cpu_operators=[dict(name=op.name, overload=op.overload) for op in plan.operators],
                            planned_buffer_bytes=plan.non_const_buffer_sizes[1:]))
    validate_methods(methods, classes)
    if len(delegate_memory) != 1:
        raise ValueError("Expected exactly one ASL Vela payload")
    # Follow the actual output-producing instruction, not a random scalar
    # elsewhere in the network. Require the known int8 -> float boundary ABI.
    plan = program.execution_plan[0]
    calls = [i.instr_args for c in plan.chains for i in c.instructions
             if isinstance(i.instr_args, KernelCall) and i.instr_args.args[-1:] == plan.outputs]
    if len(calls) != 1:
        raise ValueError("ASL output must have one dequantization producer")
    call = calls[0]
    op = plan.operators[call.op_index]
    if (op.name, op.overload) != ("cortex_m::dequantize_per_tensor", "out") or len(call.args) != 8:
        raise ValueError("Unexpected ASL output dequantization instruction")
    source, scale, zero, minimum, maximum, dtype, out, returned = [plan.values[i].val for i in call.args]
    if (not isinstance(source, Tensor) or source.scalar_type != ScalarType.CHAR or source.sizes != [1, len(classes)] or
            not isinstance(scale, Double) or any(not isinstance(v, Int) for v in (zero, minimum, maximum, dtype)) or
            not -128 <= zero.int_val <= 127 or (minimum.int_val, maximum.int_val, dtype.int_val) != (-128, 127, 1) or
            call.args[-2] != call.args[-1]):
        raise ValueError("Invalid ASL int8 output scale/zero point/interface")
    return dict(schema_version=program.version, methods=methods, delegate_memory=delegate_memory,
                output_scale=output_scale(scale.double_val), output_zero_point=zero.int_val,
                pte_sha256=hashlib.sha256(binary).hexdigest(), pte_bytes=len(binary), hardware_verified=False)


def memory_report(metadata, stored_image_metadata, classes=LEGACY_CLASSES):
    """Retain the proven reservations; do not reduce them to the new minima."""
    from tools import stored_image_firmware as stored

    validate_methods(metadata["methods"], classes)
    used = 0
    for size in metadata["methods"][0]["planned_buffer_bytes"]:
        used = (used + 31) // 32 * 32 + size
    scratch = metadata["delegate_memory"][0]["scratch_bytes"]
    # Same conservative extra allowance as the existing ImageNet preflight.
    temporary_required = (scratch + 31) // 32 * 32 + 65536
    combined_models = metadata["pte_bytes"] + stored_image_metadata["pte_bytes"]
    return dict(arena_required_bytes=used, arena_reserved_bytes=stored.ARENA,
                scratch_bytes=scratch, temporary_required_bytes=temporary_required,
                temporary_reserved_bytes=stored.TEMPORARY,
                method_reserved_bytes=stored.METHOD,
                fits_existing_runtime_pools=used <= stored.ARENA and temporary_required <= stored.TEMPORARY,
                application_mram_limit=stored.PROFILE.limits["mram_bytes"],
                two_models_alone_bytes=combined_models,
                two_models_alone_fit_mram=combined_models <= stored.PROFILE.limits["mram_bytes"],
                actual_firmware_link_required=True, hardware_verified=False)
