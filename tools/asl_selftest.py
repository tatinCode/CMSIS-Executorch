"""Approved post-hoc ASL engineering gate, not recognition/deployment approval."""

import math
import struct
from tools.asl_contract import LEGACY_CLASSES, validate_classes


def output_scale(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("ASL output scale must be finite and positive")
    try:
        canonical = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError("ASL output scale must fit float32") from error
    if not math.isfinite(canonical) or canonical <= 0:
        raise ValueError("ASL output scale must fit positive float32")
    return canonical


def policy(scale):
    return dict(revision=2, changed_after_first_hardware_failure=True,
                output_scale=output_scale(scale), max_output_steps=8,
                min_cosine=0.999, threshold=0.8, matching_top1=True,
                matching_display_decision=True, repeats=3)


def validate_policy(manifest, reference):
    gate = manifest.get("selftest_gate")
    if (not isinstance(gate, dict) or "output_scale" not in gate or
            gate != policy(gate["output_scale"]) or reference.get("selftest_gate") != gate):
        raise ValueError("Missing/mismatched approved revision-2 ASL self-test policy")
    return gate


def displayed_decision(values, classes=LEGACY_CLASSES):
    classes = validate_classes(classes)
    if len(values) != len(classes) or not all(math.isfinite(x) for x in values):
        return "--"
    best = max(range(len(classes)), key=values.__getitem__)
    confidence = 1 / sum(math.exp(x - values[best]) for x in values)
    return classes[best] if best < len(classes) - 2 and confidence >= 0.8 else "--"


def compare(actual, expected, gate, log_rounding=0.0, classes=LEGACY_CLASSES):
    """UART rounds each float to 6 decimals; caller may allow 1e-6 error only."""
    classes = validate_classes(classes)
    if (len(actual) != len(classes) or len(expected) != len(classes) or
            not all(math.isfinite(x) for x in (*actual, *expected))):
        return False
    norm = sum(x*x for x in actual) * sum(x*x for x in expected)
    cosine = sum(x*y for x, y in zip(actual, expected)) / math.sqrt(norm) if norm else 0
    return (max(abs(x-y) for x, y in zip(actual, expected)) <=
            gate["max_output_steps"] * gate["output_scale"] + log_rounding and
            cosine >= gate["min_cosine"] and
            max(range(len(classes)), key=actual.__getitem__) == max(range(len(classes)), key=expected.__getitem__) and
            displayed_decision(actual, classes) == displayed_decision(expected, classes))
