import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('torchao'), 'Isolated PT2E environment required')
class QuantReplayTests(unittest.TestCase):
    def test_backbone_keeps_exported_batch_dimension(self):
        import torch
        from ml.asl.quant_replay import backbone
        class Example(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.head = torch.nn.Linear(4,3)
            def forward(self,x):
                q = torch.ops.quantized_decomposed.quantize_per_tensor.default(x,0.02,0,-128,127,torch.int8)
                d = torch.ops.quantized_decomposed.dequantize_per_tensor.default(q,0.02,0,-128,127,torch.int8)
                return self.head(d)
        graph = torch.export.export(Example(),(torch.randn(1,4),)).module()
        features,scale = backbone(graph)
        self.assertEqual(features(torch.randn(1,4)).shape,(1,4))
        self.assertEqual(scale,0.02)

    def test_forward_does_not_fit_observers_to_validation(self):
        import torch
        from ml.asl.quant_replay import QuantHead
        torch.manual_seed(5)
        head = QuantHead(torch.nn.Linear(4,3),0.02)
        training = torch.randn(12,4)
        head.observe_train(training)
        before = (head.scale,head.zero)
        head(torch.full((3,4),1000.))
        self.assertEqual(before,(head.scale,head.zero))
        self.assertTrue(torch.isfinite(head(training)).all())

    def test_rounding_keeps_gradients_and_int8_grid(self):
        import torch
        from ml.asl.quant_replay import rounded, QuantHead
        value = torch.tensor([0.21,0.39],requires_grad=True)
        result = rounded(value,0.1)
        self.assertTrue(torch.allclose(result,torch.tensor([0.2,0.4])))
        result.sum().backward()
        self.assertTrue(torch.equal(value.grad,torch.ones_like(value)))
        head = QuantHead(torch.nn.Linear(4,3),0.02)
        x = torch.randn(10,4)
        head.observe_train(x)
        out = head(x)
        torch.nn.functional.cross_entropy(out,torch.zeros(10,dtype=torch.long)).backward()
        self.assertTrue(torch.isfinite(head.linear.weight.grad).all())
        self.assertTrue(torch.allclose(out/head.scale,(out/head.scale).round(),atol=1e-5))
