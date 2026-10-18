"""Small export/delegation check; this is not an image or handshape classifier."""

import torch


class DoubleInput(torch.nn.Module):
    def forward(self, value):
        return value + value


ModelUnderTest = DoubleInput().eval()
ModelInputs = (torch.linspace(-1.0, 1.0, 16).reshape(1, 16),)
