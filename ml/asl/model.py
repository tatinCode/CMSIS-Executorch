"""Shared training/inference contract. No implicit pretrained downloads."""

import torch
from torch import nn
from torchvision import transforms
from torchvision.models import mobilenet_v2
from torchvision.transforms import InterpolationMode
from PIL import ImageOps

from .data import CLASSES
from tools.asl_contract import STATIC_CLASSES


def contract(size):
    return dict(size=size, crop="center_square", resize="PIL_bilinear", color="RGB",
                exif_transpose=True, layout="NCHW", dtype="float32", divisor=255,
                mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])


def square_rgb(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    return image.crop((left, top, left + side, top + side))


def transform(size, augment=False):
    operations = [transforms.Lambda(square_rgb),
                  transforms.Resize((size, size), interpolation=InterpolationMode.BILINEAR)]
    if augment:
        # No horizontal flips: retain the dataset's handedness convention.
        operations += [transforms.RandomAffine(8, translate=(0.04, 0.04), scale=(0.95, 1.05),
                                                interpolation=InterpolationMode.BILINEAR, fill=127),
                       transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.1)]
    operations += [transforms.ToTensor(), transforms.Normalize(contract(size)["mean"], contract(size)["std"])]
    return transforms.Compose(operations)


ADAPTED_CLASSES = (*CLASSES, "OTHER")


def make_model(weights=None, classes=CLASSES):
    model = mobilenet_v2(weights=None)
    if weights is not None:
        model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True), strict=True)
    model.classifier = nn.Sequential(nn.Dropout(0.2), nn.Linear(model.last_channel, len(classes)))
    return model


def load_checkpoint(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    schema = checkpoint.get("schema")
    if schema not in (1, 2, 3) or checkpoint.get("architecture") != "mobilenet_v2":
        raise ValueError("Unsupported checkpoint")
    classes = CLASSES if schema == 1 else ADAPTED_CLASSES if schema == 2 else STATIC_CLASSES
    if checkpoint.get("classes") != list(classes):
        raise ValueError("Checkpoint label order differs from the ASL contract")
    size = checkpoint["preprocessing"]["size"]
    if checkpoint["preprocessing"] != contract(size):
        raise ValueError("Checkpoint preprocessing differs from the implemented contract")
    model = make_model(classes=classes)
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    return model.eval(), checkpoint
