import copy
from pathlib import Path
import tempfile
import unittest

try:
    import torch
    from torch import nn
    from torch.utils.data import TensorDataset
    from ml.asl import finetune
    from ml.asl.model import ADAPTED_CLASSES, make_model
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


@unittest.skipUnless(HAS_TORCH, "Use the ASL environment")
class FineTuneTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(2026)

    def test_only_final_convolution_and_head_are_trainable_bn_is_frozen(self):
        model = make_model(classes=ADAPTED_CLASSES)
        parameters = finetune.configure_trainable(model)
        names = {n for n, p in model.named_parameters() if p.requires_grad}
        self.assertEqual(names, {"features.18.0.weight", "classifier.1.weight", "classifier.1.bias"})
        self.assertEqual(len(parameters), 1)
        for module in model.modules():
            if isinstance(module, nn.BatchNorm2d):
                self.assertFalse(module.training)
                self.assertFalse(any(p.requires_grad for p in module.parameters()))

    def test_prefix_cache_matches_full_model_and_gradient_step_changes_only_allowed_weights(self):
        model = make_model(classes=ADAPTED_CLASSES)
        block = finetune.configure_trainable(model)
        names = {n for n, p in model.named_parameters() if p.requires_grad}
        initial = copy.deepcopy(model.state_dict())
        images, targets = torch.randn(2, 3, 128, 128), torch.tensor([0, 6])
        values, labels = finetune.cache_prefix(model, TensorDataset(images, targets), 2)
        self.assertFalse(values.requires_grad)
        self.assertEqual(tuple(values.shape), (2, 320, 4, 4))
        self.assertTrue(torch.equal(labels, targets))
        with torch.no_grad():
            self.assertTrue(torch.allclose(model(images), finetune.tail_logits(model, values), atol=1e-6))
        optimizer = torch.optim.Adam([dict(params=block, lr=1e-5),
                                      dict(params=model.classifier.parameters(), lr=1e-4)])
        model.classifier.train()
        loss = nn.functional.cross_entropy(finetune.tail_logits(model, values), targets)
        loss.backward()
        optimizer.step()
        changed = finetune.verify_frozen(model, initial, names)
        self.assertIn("features.18.0.weight", changed)
        self.assertIn("classifier.1.bias", changed)
        self.assertTrue(all(not m.training for m in model.modules() if isinstance(m, nn.BatchNorm2d)))
        # Guard also catches an accidental BN buffer update.
        model.features[18][1].running_mean.add_(1)
        with self.assertRaisesRegex(ValueError, "BatchNorm state changed"):
            finetune.verify_frozen(model, initial, names)

    def test_existing_output_is_never_overwritten(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "new fine-tuning output"):
                finetune.run(Namespace(output=Path(directory)))


if __name__ == "__main__":
    unittest.main()
