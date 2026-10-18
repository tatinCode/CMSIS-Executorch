import math
import unittest

from tools import asl_selftest as gate


class SelftestPolicyTests(unittest.TestCase):
    def test_positive_finite_float32_scale_only(self):
        self.assertEqual(gate.output_scale(0.125), 0.125)
        for scale in (None, True, '0.1', 0, -1, math.nan, math.inf, 1e40, 1e-50):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                gate.output_scale(scale)

    def test_gate_records_posthoc_change_without_changing_recognition_threshold(self):
        policy = gate.policy(0.125)
        self.assertTrue(policy['changed_after_first_hardware_failure'])
        self.assertEqual(policy['max_output_steps'], 8)
        self.assertEqual(policy['threshold'], 0.8)
        self.assertEqual(policy['min_cosine'], 0.999)
        self.assertTrue(policy['matching_display_decision'])
        gate.validate_policy(dict(selftest_gate=policy), dict(selftest_gate=policy))

    def test_negative_classes_and_uncertain_outputs_never_become_letters(self):
        self.assertEqual(gate.displayed_decision([0,0,0,0,0,20,0]), '--')
        self.assertEqual(gate.displayed_decision([0,0,0,0,0,0,20]), '--')
        self.assertEqual(gate.displayed_decision([0]*7), '--')
        self.assertEqual(gate.displayed_decision([20,0,0,0,0,0,0]), 'A')
        self.assertFalse(gate.compare([1]*6, [1]*7, gate.policy(0.125)))
