"""Build a separate TEST UI with the whole screen rotated 180 degrees. Never programs hardware.

Reuse the verified camera generator in a private module instance. Generate only
private source variants; do not edit the working capture sources, assets, build,
package pointers or its generator. This target contains NO ASL inference.
"""

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from tools import camera_inference_firmware as camera
from tools import inference as common
from tools import memory_preflight as reports


WORKSPACE = common.WORK / "camera-overlay-screen-firmware"
HEADER = common.ROOT / "firmware/camera_overlay/include/asl_display.h"
ROTATION_HEADER = common.ROOT / "firmware/camera_overlay/include/camera_rotation.h"
OVERLAY = dict(test_only=True, live_asl=False, symbols=["--", "A", "B", "C", "L", "Y"],
               interval_ms=1500, label="TEST UI / NOT LIVE ASL",
               region=dict(x=0, y=480, width=480, height=320),
               screen_rotation_degrees=180, rotation_stage="after camera and panel composition",
               capture_protocol=4, capture_rotation_degrees=0)


def preview_variant(text):
    if '#include "asl_display.h"' in text:
        raise common.SetupError("Overlay already injected")
    text = camera.smoke.replace_once(text, '#include "preview_pixels.h"',
                                     '#include "preview_pixels.h"\n#include "asl_display.h"\n#include "camera_rotation.h"')
    if text.count("handgpt::preview_rgb565(") != 1:
        raise common.SetupError("Expected exactly one preview conversion call")
    needle = "    clean(g_preview_lcd[next], sizeof(g_preview_lcd[next]));"
    return camera.smoke.replace_once(text, needle,
        "    // Deterministic glyph exercise, NOT camera recognition. CPU owns the inactive LCD buffer.\n"
        "    const auto symbol = static_cast<handgpt::AslLetter>((smoke_millis() / 1500u) % 6u);\n"
        "    if (!handgpt::draw_asl_panel(g_preview_lcd[next], 480 * 800, symbol, true)) { return false; }\n"
        "    if (!handgpt::rotate_lcd180(g_preview_lcd[next], 480 * 800)) { return false; }\n" + needle)


def main_variant(text):
    return camera.smoke.replace_once(text,
        "HandGPT camera/display + STORED-image self-test (NOT camera classification)",
        "HandGPT TEST UI: cycling -- A B C L Y; NOT LIVE ASL; LCD rotated 180 degrees; v4 capture unchanged; stored-image self-test unchanged")


def private_generator():
    spec = importlib.util.spec_from_file_location("tools._camera_overlay_generator", camera.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WORKSPACE = WORKSPACE
    original_configure = module.configure

    def configure(pack, runtime):
        sources, linker = original_configure(pack, runtime)
        generated = WORKSPACE / "generated"
        replacements = {}
        for name, convert in (("preview.cpp", preview_variant), ("main.cpp", main_variant)):
            source = module.APPLICATION / "src" / name
            target = generated / name
            target.write_text(convert(source.read_text()))
            replacements[source] = target
        project_path = WORKSPACE / "camera-inference.cproject.yml"
        project = json.loads(project_path.read_text())
        import os
        for group in project["project"]["groups"]:
            for entry in group["files"]:
                path = (WORKSPACE / entry["file"]).resolve()
                if path in replacements:
                    entry["file"] = os.path.relpath(replacements[path], WORKSPACE)
        project["project"]["add-path"].append(os.path.relpath(HEADER.parent, WORKSPACE))
        project_path.write_text(json.dumps(project, indent=2) + "\n")
        # Include original inputs and UI/rotation headers in source provenance,
        # as well as the actual generated sources compiled by the target.
        return ([replacements.get(p, p) for p in sources] + list(replacements) +
                [HEADER, ROTATION_HEADER, Path(__file__)], linker)

    module.configure = configure
    return module


def build(args):
    if args.jobs != 1:
        raise common.SetupError("The overlay target is restricted to one build job")
    # Refuse overwrite; the known capture build and package remain usable.
    if WORKSPACE.exists():
        raise common.SetupError("Overlay workspace already exists; retain it and review before rebuilding")
    input_paths = [Path(__file__), Path(camera.__file__), HEADER, ROTATION_HEADER]
    input_paths += list(camera.APPLICATION.rglob("*.cpp")) + list(camera.APPLICATION.rglob("*.h"))
    before = {str(p.relative_to(common.ROOT)): common.sha256(p) for p in input_paths}
    generator = private_generator()
    generator.build(args)
    manifest_path = WORKSPACE / "manifest.json"
    try:
        meta = reports.read_json(manifest_path)
        if any(common.sha256(common.ROOT / p) != digest for p, digest in before.items()):
            raise common.SetupError("Overlay input changed during build")
        meta.update(variant="test-ui-only", overlay_input_sha256=before, overlay=OVERLAY,
                     scope="TEST UI glyph cycling on inactive RGB565 LCD buffer; whole LCD rotated 180 degrees; v4 capture and stored-image self-test unchanged; NO ASL camera inference",
                    programming_authorized=False)
        reports.write_report(manifest_path, meta)
        print("TEST UI BUILD PASSED; NOT LIVE ASL. No package prepared or hardware accessed.")
    except Exception:
        manifest_path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--layout", choices=[camera.image.PROFILE.name], required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--jobs", type=common.positive_jobs, default=1)
    parser.add_argument("--cbuild")
    parser.add_argument("--toolchain-bin")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    try:
        common.WORK.mkdir(parents=True, exist_ok=True)
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            build(args)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, common.SetupError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
