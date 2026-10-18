#!/usr/bin/env python3
"""Prepare one consented/local image and host reference; never captures a camera."""

import argparse
import hashlib
import json
from pathlib import Path


def float_literal(value):
    text = format(float(value), ".9g")
    if "." not in text and "e" not in text.lower():
        text += ".0"
    return text + "f"


def write_float_header(values, destination):
    with Path(destination).open("w") as stream:
        stream.write("#pragma once\n#include <stdint.h>\n\n")
        stream.write("// float32, NCHW [1, 3, 224, 224]; not the legacy int8 input_image.\n")
        stream.write("alignas(16) static const float reference_input[] = {\n")
        for offset in range(0, len(values), 8):
            stream.write("    " + ", ".join(float_literal(v) for v in values[offset:offset + 8]) + ",\n")
        stream.write("};\n")
        stream.write(f"static const unsigned int reference_input_numel = {len(values)};\n")


def prepare(image_path, output):
    # Imported here so CLI help and the lightweight unit tests need no ML packages.
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    from torchvision import transforms
    from torchvision.models import MobileNet_V2_Weights, mobilenet_v2

    image_path = Path(image_path).resolve(strict=True)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    preprocess = transforms.Compose([
        transforms.Resize(256, interpolation=transforms.InterpolationMode.BILINEAR, antialias=True),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    tensor = preprocess(image).unsqueeze(0).contiguous()
    values = tensor.numpy()
    if values.shape != (1, 3, 224, 224) or values.dtype != np.float32:
        raise ValueError("Unexpected preprocessed shape or dtype")
    if not np.isfinite(values).all():
        raise ValueError("Non-finite input values")

    weights = MobileNet_V2_Weights.IMAGENET1K_V2
    model = mobilenet_v2(weights=weights).eval()
    with torch.inference_mode():
        logits = model(tensor)
    if logits.shape != (1, 1000) or not torch.isfinite(logits).all():
        raise ValueError("Unexpected host output")
    top = torch.topk(logits[0], 5)
    np.save(output / "input.npy", values, allow_pickle=False)
    np.save(output / "host_logits.npy", logits.numpy(), allow_pickle=False)
    raw = values.astype("<f4", copy=False).tobytes(order="C")
    (output / "input.f32").write_bytes(raw)
    write_float_header(values.reshape(-1), output / "reference_input.h")
    report = {
        "source_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "weights": "MobileNet_V2_Weights.IMAGENET1K_V2",
        "weights_url": weights.url,
        "preprocessing": {
            "exif_transpose": True, "color": "RGB", "resize_short_edge": 256,
            "resize": "bilinear, antialias=True", "center_crop": [224, 224],
            "scale": "uint8 / 255", "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225], "layout": "NCHW",
            "shape": [1, 3, 224, 224], "dtype": "float32", "raw_endianness": "little",
        },
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_npy_sha256": hashlib.sha256((output / "input.npy").read_bytes()).hexdigest(),
        "host_logits_sha256": hashlib.sha256((output / "host_logits.npy").read_bytes()).hexdigest(),
        "input_bytes": len(raw),
        "host_top5": [
            {"index": int(index), "label": weights.meta["categories"][int(index)], "logit": float(value)}
            for index, value in zip(top.indices, top.values)
        ],
        "hardware_verified": False,
    }
    (output / "reference.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["host_top5"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.image, args.output)


if __name__ == "__main__":
    main()
