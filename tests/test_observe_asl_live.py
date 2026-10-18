import unittest

from tools import observe_asl_live as observe
from tools import asl_selftest
from tools.asl_contract import LEGACY_CLASSES


class ObserveLiveTests(unittest.TestCase):
    def fixture(self):
        gate = asl_selftest.policy(0.18568913638591766)
        manifest = dict(pte_sha256="model-hash", selftest_gate=gate, classes=list(LEGACY_CLASSES))
        reference = dict(input_sha256="input-hash", host_logits=[3., -12., -9., -13., -4., -17., -4.], selftest_gate=gate)
        log = b"HandGPT LIVE ASL EXPERIMENTAL;\nmodel_sha256=model-hash input_sha256=input-hash\n"
        log += b"ASL GATE revision=2 output_scale=0.185689136386 max_output_steps=8 min_cosine=0.999 threshold=0.80 post_hoc=1\n"
        for repeat in range(3):
            log += (f"ASL SELFTEST repeat={repeat+1} max_error=0.000000 cosine=1.000000 "
                    "method_high_water=864 temporary_high_water=491520 output_steps=0.000000\n").encode()
            for i, value in enumerate(reference["host_logits"]):
                log += f"ASL LOGIT {i} actual={value} host={value}\n".encode()
        log += b"ASL SELFTEST PASS checked=7 repeats=3\nLIVE ASL START; real camera\n"
        log += b"LIVE ASL frames=10 predictions=7 display=-- latency_ms=42 fault=0 IRQs=10 jobs=10/10\n"
        log += b"LIVE ASL frames=17 predictions=14 display=A latency_ms=43 fault=0 IRQs=17 jobs=17/17\n"
        return log, manifest, reference

    def test_full_boot_and_progress_is_not_an_accuracy_or_cold_boot_claim(self):
        report = observe.assess(*self.fixture())
        self.assertTrue(report["boot_and_progress_checks_passed"])
        self.assertFalse(report["gesture_accuracy_verified"])
        self.assertFalse(report["cold_boot_verified"])

    def test_failures_stale_progress_wrong_model_and_false_reference_are_rejected(self):
        log, manifest, reference = self.fixture()
        for bad in (log.replace(b"model-hash", b"other"), log.replace(b"checked=7", b"checked=1000"),
                    log.replace(b"fault=0", b"fault=1"), log.replace(b"jobs=17/17", b"jobs=17/16"),
                    log.replace(b"predictions=14", b"predictions=7"),
                    log.replace(b"output_steps=0.000000", b"output_steps=1.000000"),
                    log.replace(b"actual=3.0", b"actual=8.0"), log.replace(b"host=3.0", b"host=4.0"),
                    log.replace(b"max_output_steps=8", b"max_output_steps=9"),
                    log.replace(b"repeat=3", b"repeat=2"),
                    log.replace(b"repeat=3 max_error=0.000000 cosine=1.000000 method_high_water=864",
                                b"repeat=3 max_error=0.000000 cosine=1.000000 method_high_water=896"),
                    log + b"LIVE ASL INFERENCE FAULT\n", log + log):
            with self.subTest(bad=bad[-100:]):
                self.assertFalse(observe.assess(bad, manifest, reference)["boot_and_progress_checks_passed"])

    def test_pre_reset_progress_cannot_contaminate_the_new_boot_counters(self):
        log, manifest, reference = self.fixture()
        old = b"LIVE ASL frames=999 predictions=999 display=A latency_ms=43 fault=0 IRQs=999 jobs=999/999\n"
        self.assertTrue(observe.assess(old + log, manifest, reference)["boot_and_progress_checks_passed"])

    def test_missing_mismatched_or_weakened_gate_fails_closed(self):
        log, manifest, reference = self.fixture()
        for bad in (None, {}, {**manifest['selftest_gate'], 'output_scale': 0},
                    {**manifest['selftest_gate'], 'output_scale': float('nan')},
                    {**manifest['selftest_gate'], 'max_output_steps': 9}):
            self.assertFalse(observe.assess(log, {**manifest, 'selftest_gate': bad}, reference)['boot_and_progress_checks_passed'])
        self.assertFalse(observe.assess(log, manifest, {**reference, 'selftest_gate': {}})['boot_and_progress_checks_passed'])

    def test_wrong_display_decision_fails_even_when_top1_and_cosine_match(self):
        log, manifest, reference = self.fixture()
        expected = [1.375, 0., -40., -40., -40., -40., -40.]
        actual = [1.5, 0., -40., -40., -40., -40., -40.]
        for i, (old, a, h) in enumerate(zip(reference['host_logits'], actual, expected)):
            log = log.replace(f"ASL LOGIT {i} actual={old} host={old}".encode(),
                              f"ASL LOGIT {i} actual={a} host={h}".encode())
        report = observe.assess(log, manifest, {**reference, 'host_logits': expected})
        self.assertFalse(report['boot_and_progress_checks_passed'])

    def test_all_three_repeats_of_the_first_boot_values_satisfy_the_new_gate(self):
        import math
        import re
        log, manifest, reference = self.fixture()
        expected = [2.971026, -12.812551, -9.284457, -13.740996, -4.456539, -17.083401, -4.085161]
        actual = [2.971026, -12.441173, -8.727389, -13.740996, -4.827918, -17.083401, -4.085161]
        for i, (old, a, h) in enumerate(zip(reference['host_logits'], actual, expected)):
            log = log.replace(f"ASL LOGIT {i} actual={old} host={old}".encode(),
                              f"ASL LOGIT {i} actual={a} host={h}".encode())
        error = max(abs(a-h) for a,h in zip(actual, expected))
        cosine = sum(a*h for a,h in zip(actual, expected)) / math.sqrt(sum(a*a for a in actual)*sum(h*h for h in expected))
        log = re.sub(rb'max_error=0.000000 cosine=1.000000', f'max_error={error:.6f} cosine={cosine:.6f}'.encode(), log)
        log = log.replace(b'output_steps=0.000000', f'output_steps={error/manifest["selftest_gate"]["output_scale"]:.6f}'.encode())
        report = observe.assess(log, manifest, {**reference, 'host_logits': expected})
        self.assertTrue(report['boot_and_progress_checks_passed'], report['problems'])
