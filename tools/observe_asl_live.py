"""Read-only experimental live-ASL boot/progress log. Never sends or flashes."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import select
import time

from tools.capture_camera import checked_identity, listening_port
from tools import asl_selftest
from tools.asl_contract import LEGACY_CLASSES, validate_classes


PROGRESS = re.compile(rb"LIVE ASL frames=(\d+) predictions=(\d+) display=(--|[A-Z]+) latency_ms=(\d+) fault=([01]) IRQs=(\d+) jobs=(\d+)/(\d+)")
LOGIT = re.compile(rb"ASL LOGIT (\d+) actual=([-+0-9.eE]+) host=([-+0-9.eE]+)")
GATE = re.compile(rb"ASL GATE revision=2 output_scale=([-+0-9.eE]+) max_output_steps=(\d+) min_cosine=0.999 threshold=0.80 post_hoc=1")
REPEAT = re.compile(rb"ASL SELFTEST repeat=(\d+) max_error=([-+0-9.eE]+) cosine=([-+0-9.eE]+) method_high_water=(\d+) temporary_high_water=(\d+) output_steps=([-+0-9.eE]+)")


def assess(log, manifest, reference):
    problems = []
    try:
        gate = asl_selftest.validate_policy(manifest, reference)
        classes = validate_classes(manifest["classes"])
        if reference.get("classes", list(LEGACY_CLASSES)) != list(classes):
            raise ValueError("Reference class order differs from model")
        count = len(classes)
        expected = reference["host_logits"]
        if len(expected) != count or not all(math.isfinite(x) for x in expected):
            raise ValueError("Invalid class count/host reference logits")
    except (KeyError, TypeError, ValueError) as error:
        return dict(boot_and_progress_checks_passed=False, problems=[str(error)],
                    progress_records=0, selftest_logits=0, gesture_accuracy_verified=False, cold_boot_verified=False)
    banner = b"HandGPT LIVE ASL EXPERIMENTAL;"
    if log.count(banner) != 1:
        problems.append("Need exactly one complete live-ASL boot banner")
    if banner in log:
        # The board may emit progress from its previous run while the listener
        # is armed. Never compare those old counters against the reset run.
        log = log[log.rfind(banner):]
    if classes != LEGACY_CLASSES and ("classes=" + ','.join(classes)).encode() not in log:
        problems.append("Missing/mismatched firmware class order")
    if log.count(f"ASL SELFTEST PASS checked={count} repeats=3".encode()) != 1 or log.count(b"LIVE ASL START;") != 1:
        problems.append("Need exactly one full ASL boot self-test and LIVE ASL START; arm then reset once")
    elif log.index(b"ASL SELFTEST PASS") > log.index(b"LIVE ASL START;"):
        problems.append("Live started before the self-test passed")
    if manifest["pte_sha256"].encode() not in log or reference["input_sha256"].encode() not in log:
        problems.append("Missing/mismatched model or exact-reference input identity")
    if b"FAULT" in log or b"FAIL" in log:
        problems.append("Firmware reported a fault/failure")
    gates = GATE.findall(log)
    if (len(gates) != 1 or not math.isfinite(float(gates[0][0])) or abs(float(gates[0][0]) - gate["output_scale"]) > 1e-12 or
            int(gates[0][1]) != gate["max_output_steps"]):
        problems.append("Missing/mismatched revision-2 firmware gate identity")
    repeats = REPEAT.findall(log)
    if (len(repeats) != 3 or [int(r[0]) for r in repeats] != [1, 2, 3] or
            len({(r[3], r[4]) for r in repeats}) != 1 or
            any(int(r[3]) == 0 or int(r[4]) == 0 for r in repeats)):
        problems.append("Need three ordered self-test repeats with stable allocator high-water marks")
    logits = LOGIT.findall(log)
    if len(logits) != 3 * count:
        problems.append(f"Need {count} logged outputs in each of three self-test repeats")
    else:
        for repeat in range(3):
            actual = []
            for index, (number, value, host) in enumerate(logits[repeat * count:(repeat + 1) * count]):
                a, h = float(value), float(host)
                expected_value = expected[index]
                if int(number) != index or not math.isfinite(a) or not math.isfinite(h) or abs(h - expected_value) > 1e-5:
                    problems.append("Self-test logit index/reference mismatch")
                actual.append(a)
            # Device uses full float32 values; UART prints 6 decimal places.
            # Allow <=1e-6 absolute rounding only, not another output step.
            if not asl_selftest.compare(actual, expected, gate, log_rounding=1e-6, classes=classes):
                problems.append("Device self-test outputs fail revision-2 scale/top-1/display/cosine checks")
            if len(repeats) == 3:
                metrics = [float(repeats[repeat][i]) for i in (1, 2, 5)]
                error = max(abs(x-y) for x, y in zip(actual, expected))
                norm = sum(x*x for x in actual) * sum(x*x for x in expected)
                cosine = sum(x*y for x, y in zip(actual, expected)) / math.sqrt(norm) if norm else 0
                if (not all(math.isfinite(v) for v in metrics) or
                        abs(metrics[0] - error) > 2e-6 or abs(metrics[1] - cosine) > 2e-6 or
                        # One micro-logit of UART rounding is not a fixed
                        # number of steps when a different model scale is used.
                        abs(metrics[2] - error / gate["output_scale"]) > 1e-6 / gate["output_scale"] + 1e-6):
                    problems.append("Logged self-test metrics disagree with the output logits")
    start = log.find(b"LIVE ASL START;")
    rows = PROGRESS.findall(log[start:]) if start >= 0 else []
    if len(rows) < 2:
        problems.append("Need at least two live progress records")
    else:
        first, last = rows[0], rows[-1]
        if int(last[0]) <= int(first[0]) or int(last[1]) <= int(first[1]):
            problems.append("Camera/prediction progress did not increase")
        if any(int(r[4]) or int(r[5]) == 0 or int(r[6]) != int(r[7]) for r in rows):
            problems.append("Fault, missing IRQ completion, or unmatched NPU jobs in live progress")
        if any(r[2].decode() not in (*classes[:-2], "--") for r in rows):
            problems.append("Unsupported letter in live progress")
    return dict(boot_and_progress_checks_passed=not problems, problems=problems,
                progress_records=len(rows), selftest_logits=len(logits),
                gesture_accuracy_verified=False, cold_boot_verified=False)


def observe(args):
    manifest = json.loads((args.export / "manifest.json").read_text())
    reference = json.loads((args.export / "reference/reference.json").read_text())
    asl_selftest.validate_policy(manifest, reference) # Fail before opening UART for stale/missing policy.
    classes = validate_classes(manifest['classes'])
    if (reference.get('classes', list(LEGACY_CLASSES)) != list(classes) or
            len(reference.get('host_logits', [])) != len(classes)):
        raise ValueError('Missing/mismatched reference class order/count')
    identity = checked_identity(args.port)
    if not 0 < args.seconds <= 300:
        raise ValueError("Use a listening duration from 0 to 300 seconds, exclusive of zero")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd_log = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    log = bytearray()
    try:
        with listening_port(args.port, identity) as fd:
            print(f"READ ONLY: {args.port}; nothing sent. Press board RESET once now.", flush=True)
            deadline, displayed = time.monotonic() + args.seconds, 0
            while time.monotonic() < deadline:
                if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
                    break
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                log.extend(chunk)
                if len(log) > 2 * 1024 * 1024:
                    raise ValueError("UART log exceeded 2 MiB; wrong firmware or unexpected stream")
                if displayed < 16384:
                    excerpt = chunk[:16384 - displayed]
                    print(f"UART {excerpt!r}", flush=True)
                    displayed += len(excerpt)
    finally:
        with os.fdopen(fd_log, "wb") as handle:
            handle.write(log)
    report = assess(bytes(log), manifest, reference)
    print(json.dumps(report, indent=2))
    print(f"Saved {args.output}; screen behavior/gesture accuracy and cold-boot checks remain manual.")
    return 0 if report["boot_and_progress_checks_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--export", type=Path, default=Path(".work/asl/static-export-v1"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return observe(args)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
