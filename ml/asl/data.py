"""Reproducible, host-only inventory and conservative dataset splitting."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re


CLASSES = ("A", "B", "C", "L", "Y", "NO_HAND")
FOLDERS = ("A", "B", "C", "L", "Y", "nothing")
SPLITS = ("train", "val", "test")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class HashIndex:
    """64-bit dHash radius-two search using three pigeonhole buckets."""

    def __init__(self):
        self.buckets = defaultdict(set)

    @staticmethod
    def keys(value):
        return ((0, value & ((1 << 22) - 1)),
                (1, (value >> 22) & ((1 << 21) - 1)), (2, value >> 43))

    def add(self, value):
        for key in self.keys(value):
            self.buckets[key].add(value)

    def contains_near(self, value):
        return any((value ^ candidate).bit_count() <= 2
                   for key in self.keys(value) for candidate in self.buckets.get(key, ()))


def dhash(image):
    from PIL import Image
    pixels = list(image.convert("L").resize((9, 8), Image.Resampling.BILINEAR).get_flattened_data())
    value = 0
    for y in range(8):
        for x in range(8):
            value = (value << 1) | (pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    return value


def split_records(records, seed, block_size, classes=CLASSES):
    """Number blocks are conservative proxies, NOT verified signer/session IDs.

    Assign complete blocks first. Quarantine evaluation images resembling any
    retained earlier split (train > val > test). Never move rejected evaluation
    images into training, and never use labels to decide perceptual similarity.
    """
    if block_size < 1:
        raise ValueError("block_size must be positive")
    candidates = {split: [] for split in SPLITS}
    for label in classes:
        groups = defaultdict(list)
        for record in records:
            if record["label"] == label:
                record = dict(record, group=f"{label}:{(record['number'] - 1) // block_size}")
                groups[record["group"]].append(record)
        keys = sorted(groups)
        if len(keys) < 5:
            raise ValueError(f"Too few numbered groups for {label}: {len(keys)}")
        random.Random(f"{seed}:{label}").shuffle(keys)
        heldout = max(1, round(len(keys) * 0.15))
        for i, key in enumerate(keys):
            split = "val" if i < heldout else "test" if i < 2 * heldout else "train"
            candidates[split].extend(groups[key])
    index = HashIndex()
    seen_pixels = set()
    retained, quarantined = [], []
    for split in SPLITS:
        current = []
        for record in sorted(candidates[split], key=lambda r: r["path"]):
            if record["pixels_sha256"] in seen_pixels or index.contains_near(record["dhash"]):
                quarantined.append(dict(record, proposed_split=split, reason="similar_to_earlier_split"))
            else:
                current.append(dict(record, split=split))
        # Within-split similarity is allowed; cross-split matches are removed.
        for record in current:
            index.add(record["dhash"])
            seen_pixels.add(record["pixels_sha256"])
        retained.extend(current)
    counts = Counter((r["split"], r["label"]) for r in retained)
    for split in SPLITS:
        for label in classes:
            if counts[split, label] < 20:
                raise ValueError(f"Only {counts[split, label]} retained {split}/{label} images; inspect grouping")
    return retained, quarantined


def prepare(args, classes=CLASSES, folders=FOLDERS):
    from PIL import Image, ImageDraw, ImageOps
    root, output = args.root.resolve(), args.output.resolve()
    if output.exists():
        raise ValueError(f"Refusing to overwrite {output}; select a new output directory")
    records, duplicates, seen = [], [], {}
    if len(classes) != len(folders) or len(set(classes)) != len(classes):
        raise ValueError("Classes and folders must have a unique one-to-one mapping")
    for label, folder in zip(classes, folders):
        paths = sorted((root / folder).glob("*.jpg"))
        if not paths:
            raise ValueError(f"No JPEG images in {root / folder}")
        for path in paths:
            match = re.fullmatch(re.escape(folder) + r"(\d+)\.jpg", path.name)
            if not match or int(match[1]) < 1:
                raise ValueError(f"Unexpected filename; cannot infer numbered block: {path}")
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.load()  # Decode every file; corruption aborts preparation.
            pixel_hash = hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()
            record = dict(path=str(path.relative_to(root)), label=label, number=int(match[1]),
                          sha256=sha256(path), pixels_sha256=pixel_hash, dhash=dhash(image),
                          width=image.width, height=image.height)
            if pixel_hash in seen:
                previous = seen[pixel_hash]
                if previous["label"] != label:
                    raise ValueError(f"Identical pixels with conflicting labels: {previous['path']} / {record['path']}")
                duplicates.append(dict(record, duplicate_of=previous["path"]))
            else:
                seen[pixel_hash] = record
                records.append(record)
        print(f"Inventoried {folder}: {len(paths)} images", flush=True)
    retained, quarantined = split_records(records, args.seed, args.block_size, classes)
    summary = {
        "counts": {split: {label: sum(r["split"] == split and r["label"] == label for r in retained)
                           for label in classes} for split in SPLITS},
        "exact_duplicates_removed": len(duplicates), "near_duplicate_eval_images_quarantined": len(quarantined),
        "signer_independent": False,
        "limitations": "Filename blocks are not verified sessions. dHash screening is heuristic, not proof of no leakage.",
    }
    manifest = dict(schema=1, root=str(root), classes=list(classes), seed=args.seed,
                    block_size=args.block_size, dhash_radius=2, summary=summary, records=retained,
                    duplicates=duplicates, quarantined=quarantined)
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Six spread-out examples per class to inspect labels/backgrounds visually.
    sheet = Image.new("RGB", (6 * 160, len(classes) * 180), "white")
    draw = ImageDraw.Draw(sheet)
    for row, label in enumerate(classes):
        examples = sorted((r for r in retained if r["label"] == label), key=lambda r: r["number"])
        for col in range(6):
            record = examples[col * (len(examples) - 1) // 5]
            with Image.open(root / record["path"]) as source:
                image = ImageOps.exif_transpose(source).convert("RGB").resize((160, 160))
            sheet.paste(image, (col * 160, row * 180))
            draw.text((col * 160 + 2, row * 180 + 162), f"{record['path']} [{record['split']}]", fill="black")
    sheet.save(output / "samples.jpg")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("asl_alphabet_train/asl_alphabet_train"))
    parser.add_argument("--output", type=Path, default=Path(".work/asl/dataset-v1"))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--block-size", type=int, default=100)
    args = parser.parse_args()
    try:
        prepare(args)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
