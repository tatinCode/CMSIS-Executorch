"""Audit CRC-checked camera captures and assign whole development sessions.

Never edits captures, relabels a pose, or creates a final test split. UNKNOWN is
mapped to the candidate's learned OTHER class, not to NO_HAND.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import zlib

from PIL import Image, ImageDraw

from .data import CLASSES, dhash, sha256
from .model import square_rgb
from tools.capture_camera import as_image


CAMERA_CLASSES = (*CLASSES, "OTHER")
CAPTURE_LABELS = (*CLASSES, "UNKNOWN")


def write_json(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def inventory_session(root, session, split):
    if (not session or Path(session).name != session or session in (".", "..") or
            split not in ("train", "val")):
        raise ValueError("Use a simple session name and train/val assignment")
    directory = root / session
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"Missing or symlinked camera session: {session}")
    records = []
    for folder in sorted(directory.iterdir()):
        if folder.name not in CAPTURE_LABELS or not folder.is_dir() or folder.is_symlink():
            raise ValueError(f"Unexpected camera label folder: {folder}")
        stems = set()
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix not in (".json", ".png", ".rgb", ".log"):
                raise ValueError(f"Unexpected capture file: {path}")
            stems.add(path.stem)
        for stem in sorted(stems):
            base = folder / stem
            siblings = {ext: base.with_suffix(ext) for ext in (".json", ".png", ".rgb", ".log")}
            if any(not path.is_file() for path in siblings.values()):
                raise ValueError(f"Incomplete capture group: {base}")
            metadata = json.loads(siblings[".json"].read_text())
            raw = siblings[".rgb"].read_bytes()
            profile = {3: (480, 480, 691200), 4: (128, 128, 49152)}.get(metadata.get("capture_protocol"))
            if (not profile or (metadata.get("width"), metadata.get("height"), len(raw)) != profile or
                    metadata.get("format") != "RGB888P" or metadata.get("session") != session or
                    metadata.get("label") != folder.name or metadata.get("split") != "unassigned"):
                raise ValueError(f"Capture profile/session/label/split mismatch: {base}")
            if (sha256(siblings[".png"]) != metadata.get("png_sha256") or
                    hashlib.sha256(raw).hexdigest() != metadata.get("raw_sha256") or
                    f"{zlib.crc32(raw):08x}" != metadata.get("crc32")):
                raise ValueError(f"Capture hash/CRC mismatch: {base}")
            with Image.open(siblings[".png"]) as source:
                source.load()
                if source.mode != "RGB" or source.size != profile[:2] or source.tobytes() != as_image(raw, *profile[:2]).tobytes():
                    raise ValueError(f"PNG/raw pixel mismatch: {base}")
                image = square_rgb(source).resize((128, 128), Image.Resampling.BILINEAR)
                fingerprint = dhash(image)
                pixels = hashlib.sha256(image.tobytes()).hexdigest()
            records.append(dict(path=str(siblings[".png"].relative_to(root)), session=session, split=split,
                                label="OTHER" if folder.name == "UNKNOWN" else folder.name,
                                capture_label=folder.name, sha256=metadata["png_sha256"],
                                raw_sha256=metadata["raw_sha256"], metadata_sha256=sha256(siblings[".json"]),
                                log_sha256=sha256(siblings[".log"]), normalized_sha256=pixels,
                                dhash=fingerprint, captured_at=metadata["captured_at"],
                                width=profile[0], height=profile[1], capture_protocol=metadata["capture_protocol"]))
    if not records:
        raise ValueError(f"Empty session: {session}")
    return records


def split_inventory(records, radius=2):
    train = [r for r in records if r["split"] == "train"]
    val = [r for r in records if r["split"] == "val"]
    if not train or not val or not 0 <= radius <= 2:
        raise ValueError("Need training and validation sessions and dHash radius 0..2")
    sessions = {}
    paths = set()
    pixels = {}
    for record in records:
        if record["path"] in paths or record["label"] not in CAMERA_CLASSES:
            raise ValueError("Repeated camera path or invalid class")
        paths.add(record["path"])
        if record["session"] in sessions and sessions[record["session"]] != record["split"]:
            raise ValueError("A camera session cannot cross splits")
        sessions[record["session"]] = record["split"]
        key = record["normalized_sha256"]
        if key in pixels and pixels[key] != record["label"]:
            raise ValueError("Identical normalized pixels have conflicting labels; review the captures")
        pixels[key] = record["label"]
    kept, quarantined = [], []
    for record in val:
        matches = [t["path"] for t in train if t["normalized_sha256"] == record["normalized_sha256"] or
                   (t["dhash"] ^ record["dhash"]).bit_count() <= radius]
        if matches:
            quarantined.append(dict(record, reason="similar_to_training_session", matches=matches))
        else:
            kept.append(record)
    retained = train + kept
    for split in ("train", "val"):
        missing = [c for c in CAMERA_CLASSES if not any(r["split"] == split and r["label"] == c for r in retained)]
        if missing:
            raise ValueError(f"Missing {split} classes after screening: {missing}; collect data, do not weaken the split")
    return retained, quarantined


def contact_sheet(root, records, path):
    rows = [sorted((r for r in records if r["label"] == c), key=lambda r: r["path"]) for c in CAMERA_CLASSES]
    columns = max(len(row) for row in rows)
    sheet = Image.new("RGB", (columns * 144, len(rows) * 172), "white")
    draw = ImageDraw.Draw(sheet)
    for y, row in enumerate(rows):
        for x, record in enumerate(row):
            with Image.open(root / record["path"]) as image:
                image = square_rgb(image).resize((128, 128), Image.Resampling.BILINEAR)
            sheet.paste(image, (x * 144, y * 172))
            draw.text((x * 144, y * 172 + 130), f"{record['capture_label']} #{x + 1}", fill="black")
            draw.text((x * 144, y * 172 + 144), Path(record["path"]).stem[:16], fill="black")
    sheet.save(path)


def prepare(args):
    root = args.root.resolve(strict=True)
    if args.output.exists():
        raise ValueError("Choose a new audit output directory; existing audits are retained")
    if set(args.train_session) & set(args.val_session) or len(set(args.train_session + args.val_session)) != len(args.train_session + args.val_session):
        raise ValueError("Sessions must be unique and disjoint between training and validation")
    records = []
    for split, sessions in (("train", args.train_session), ("val", args.val_session)):
        for session in sessions:
            records.extend(inventory_session(root, session, split))
    retained, quarantined = split_inventory(records)
    duplicates = {}
    for record in records:
        duplicates.setdefault(record["normalized_sha256"], []).append(record["path"])
    summary = dict(total_captures=len(records), retained=len(retained), quarantined=len(quarantined),
                   counts={split: dict(Counter(r["label"] for r in retained if r["split"] == split)) for split in ("train", "val")},
                   sessions={s: dict(Counter(r["label"] for r in records if r["session"] == s))
                             for s in args.train_session + args.val_session},
                   exact_duplicate_groups=[p for p in duplicates.values() if len(p) > 1])
    manifest = dict(schema=1, kind="camera_development_sessions", root=str(root), classes=list(CAMERA_CLASSES),
                    preprocessing_size=128, train_sessions=args.train_session, val_sessions=args.val_session,
                    dhash_radius=2, summary=summary, records=retained, quarantined=quarantined,
                    label_review="pending", final_test_present=False,
                    limitations=["One presenter; session independence is user-reported, not signer independence.",
                                 "dHash screening is heuristic, not proof of independence.",
                                 "Pose labels are supplied by the presenter, not an expert ASL annotation."])
    args.output.mkdir(parents=True)
    write_json(args.output / "manifest.json", manifest)
    for session in args.train_session + args.val_session:
        contact_sheet(root, [r for r in records if r["session"] == session], args.output / f"{session}.png")
    print(json.dumps(summary, indent=2))
    print(f"Review contact sheets in {args.output}; captures were not changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=Path(".work/asl/camera"))
    parser.add_argument("--train-session", action="append", required=True)
    parser.add_argument("--val-session", action="append", required=True)
    parser.add_argument("--output", type=Path, default=Path(".work/asl/camera-audit-v1"))
    args = parser.parse_args()
    try:
        prepare(args)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
