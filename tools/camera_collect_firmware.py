"""Separate one-way continuous collection image. No training, inference or flashing."""

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path

from tools import camera_inference_firmware as camera, inference as common, memory_preflight as reports

WORKSPACE = common.WORK / "camera-collect-firmware-v1"
HEADER = common.ROOT / "firmware/camera_collect/include/collect_display.h"
FONT = common.ROOT / "firmware/camera_overlay/include/asl_display.h"
ROTATION = common.ROOT / "firmware/camera_overlay/include/camera_rotation.h"
COLLECTION = dict(protocol=5, wire_version=2, width=128, height=128, payload_bytes=49152,
                  settle_ms=2500, uart_rx=False, live_asl=False, screen_rotation_degrees=180,
                  camera_rotation_degrees=0, label_source="host only; manual review required")


def main_variant(text):
    marker = '    // One-way transport: the host listens before the user resets the board.\n'
    if text.count(marker) != 1:
        raise common.SetupError("Expected the unchanged v4 main template")
    text = text[:text.index(marker)]
    text = camera.smoke.replace_once(text, '#include "preview.h"', '#include "preview.h"\n#include "collect_display.h"')
    text = camera.smoke.replace_once(text, 'bool run_stored_image();', 'uint32_t g_collection_sequence;\nbool run_stored_image();')
    text = camera.smoke.replace_once(text, 'HandGPT camera/display + STORED-image self-test (NOT camera classification)',
                                    'HandGPT CONTINUOUS COLLECTION v=5; NOT ASL; LCD rotated 180')
    return text + '''    std::printf("COLLECT READY v=5 settle_ms=2500 payload_bytes=49152; no UART RX\\n");
    uint32_t previous_frame = preview_frames();
    while (true) {
        if (g_collection_sequence == UINT32_MAX) { fail(420); }
        ++g_collection_sequence;
        if (std::printf("HGCONT BEGIN 5 %lu\\n", static_cast<unsigned long>(g_collection_sequence)) < 0 ||
            std::fflush(stdout) != 0 || g_smoke_status.uart_timeouts) { fail(422); }
        // Fresh full frames during the pose window. Camera is stopped before TX.
        const uint32_t start = smoke_millis();
        do {
            if (!preview_frame()) { fail(208); }
        } while (static_cast<uint32_t>(smoke_millis() - start) < 2500);
        const uint32_t frame = preview_frames();
        if (frame <= previous_frame) { fail(421); }
        if (!camera_export_frame()) { fail(423); } // Never continue a broken binary stream.
        if (std::printf("HGCONT END 5 %lu %lu\\n", static_cast<unsigned long>(g_collection_sequence),
                        static_cast<unsigned long>(frame)) < 0 || std::fflush(stdout) != 0 ||
            g_smoke_status.uart_timeouts) { fail(424); }
        previous_frame = frame;
    }
}
'''


def preview_variant(text):
    text = camera.smoke.replace_once(text, '#include "preview_pixels.h"',
        '#include "preview_pixels.h"\n#include "collect_display.h"\n#include "camera_rotation.h"')
    needle = '    clean(g_preview_lcd[next], sizeof(g_preview_lcd[next]));'
    return camera.smoke.replace_once(text, needle,
        '    if (!handgpt::draw_collection_panel(g_preview_lcd[next], 480 * 800, g_collection_sequence)) { return false; }\n'
        '    if (!handgpt::rotate_lcd180(g_preview_lcd[next], 480 * 800)) { return false; }\n' + needle)


def private_generator():
    spec = importlib.util.spec_from_file_location("tools._collect_generator", camera.__file__)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generator.WORKSPACE = WORKSPACE
    original = generator.configure

    def configure(pack, runtime):
        sources, linker = original(pack, runtime)
        replacements = {}
        for name, convert in (("main.cpp", main_variant), ("preview.cpp", preview_variant)):
            source = camera.APPLICATION / "src" / name
            target = WORKSPACE / "generated" / name
            target.write_text(convert(source.read_text()))
            replacements[source] = target
        project_path = WORKSPACE / "camera-inference.cproject.yml"
        project = reports.read_json(project_path)
        for group in project['project']['groups']:
            for entry in group['files']:
                path = (WORKSPACE / entry['file']).resolve()
                entry['file'] = os.path.relpath(replacements.get(path, path), WORKSPACE)
        project['project']['add-path'] += [os.path.relpath(HEADER.parent, WORKSPACE), os.path.relpath(FONT.parent, WORKSPACE)]
        project_path.write_text(json.dumps(project, indent=2)+'\n')
        return [replacements.get(p,p) for p in sources] + list(replacements) + [HEADER,FONT,ROTATION,Path(__file__)], linker

    generator.configure = configure
    return generator


def build(args):
    if args.jobs != 1 or args.rebuild or WORKSPACE.exists():
        raise common.SetupError("Require one job, a fresh collection workspace, and no --rebuild")
    private_generator().build(args)
    path = WORKSPACE / 'manifest.json'
    try:
        meta = reports.read_json(path)
        meta.update(variant='continuous-collection', collection=COLLECTION,
                    scope='One-way repeating CRC-checked full-view capture; NOT ASL recognition; no RX; upright LCD only')
        reports.write_report(path,meta)
        from tools.prepare_camera_collect import check_build
        check_build(args.image.resolve(strict=True))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    print('CONTINUOUS COLLECTION BUILD VERIFIED; no board accessed.')


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--image',type=Path,default=common.ROOT/'ml/reference/images/cat.jpg')
    parser.add_argument('--layout',choices=[camera.image.PROFILE.name],required=True)
    parser.add_argument('--jobs',type=int,default=1)
    parser.add_argument('--cbuild')
    parser.add_argument('--toolchain-bin')
    parser.set_defaults(rebuild=False)
    args=parser.parse_args()
    with (common.WORK/'command.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        build(args)


if __name__=='__main__':
    main()
