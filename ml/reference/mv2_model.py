"""Explicit MobileNet weights and golden input for the Arm AoT compiler."""

import os

import numpy as np
import torch
from torchvision.models import MobileNet_V2_Weights, mobilenet_v2


values = np.load(os.environ["HANDGPT_REFERENCE_INPUT"], allow_pickle=False)
if values.shape != (1, 3, 224, 224) or values.dtype != np.float32:
    raise ValueError("Reference input must be a float32 NCHW tensor of shape (1, 3, 224, 224)")
if not np.isfinite(values).all():
    raise ValueError("Reference input contains non-finite values")

ModelUnderTest = mobilenet_v2(weights=MobileNet_V2_Weights.IMAGENET1K_V2).eval()
ModelInputs = (torch.from_numpy(values),)
