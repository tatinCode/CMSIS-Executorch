"""Prepare/review continuous collector packages offline, with full baseline checks."""

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path

from tools import camera_collect_firmware as collect, camera_inference_firmware as camera
from tools import inference as common, memory_preflight as reports
from tools import prepare_camera_inference as capture, prepare_stored_image as accepted, program_smoke as shared

ROOT=common.ROOT
FIRMWARE=collect.WORKSPACE
PROGRAM=common.WORK/'camera-collect-programming-v1'
PROFILE=camera.image.PROFILE
TARGET='camera-continuous-collection.debug+DevKit-E8-HP'
IMAGE_NAME='handgpt-camera-continuous-collection.bin'
CONFIG_NAME='handgpt-camera-continuous-collection.json'


def check_build(image):
    path=FIRMWARE/'manifest.json'
    meta=reports.read_json(path)
    if (meta.get('variant')!='continuous-collection' or meta.get('collection')!=collect.COLLECTION or
            meta.get('target')!=camera.NAME+'.debug+'+camera.smoke.TARGET or meta.get('capture')!=camera.CAPTURE or
            meta.get('layout_profile')!=PROFILE.report() or meta.get('executorch_revision')!=common.REVISION or
            any(meta.get(n) is not False for n in ('hardware_verified','programming_authorized','camera_classification_implemented'))):
        raise common.SetupError('Expected the dedicated continuous collector, NOT a live/capture/TEST UI image')
    binary=shared.inside(ROOT/meta['bin'],FIRMWARE)
    elf=shared.inside(ROOT/meta['elf'],FIRMWARE)
    expected=FIRMWARE/'out/camera-inference/DevKit-E8-HP/debug/camera-inference.bin'
    if binary!=expected.resolve() or elf!=expected.with_suffix('.elf').resolve():
        raise common.SetupError('Wrong collector binary/ELF paths')
    commands=elf.parent/'compile_commands.json'
    for p,digest in ((binary,meta['bin_sha256']),(elf,meta['elf_sha256']),
                     (Path(camera.__file__),meta['generator_sha256']),(commands,meta['compile_commands_sha256'])):
        if common.sha256(p)!=digest: raise common.SetupError('Collector artifact changed: '+str(p))
    sources=[camera.APPLICATION/'src'/n for n in ('main.cpp','power.cpp','preview.cpp','frame_export.cpp')]
    sources += [ROOT/'firmware/stored_image/src/model_runner.cpp',ROOT/'firmware/smoke/src/platform.cpp']
    sources += [FIRMWARE/'generated'/n for n in ('platform.cpp','main.cpp','preview.cpp')]
    sources += [camera.smoke.VENDOR/'viewfinder/camera'/n for n in ('isp_calibration.c','isp_calibration_mt9m114.c','isp_calibration.h')]
    for folder in ('firmware/camera_inference/include','firmware/smoke/include','firmware/stored_image/include'):
        sources += list((ROOT/folder).glob('*.h'))
    sources += [collect.HEADER,collect.FONT,collect.ROTATION,Path(collect.__file__)]
    accepted.identities(meta['source_sha256'],{str(p.relative_to(ROOT)) for p in sources},ROOT)
    accepted.identities(meta['tool_sha256'],capture.TOOLS,ROOT/'tools')
    accepted.identities(meta['project_sha256'],capture.PROJECTS,FIRMWARE)
    generated={str(p.relative_to(FIRMWARE)) for folder in ('assets','RTE','generated') for p in (FIRMWARE/folder).rglob('*') if p.is_file()}
    accepted.identities(meta['generated_sha256'],generated,FIRMWARE)
    for name,convert in (('main.cpp',collect.main_variant),('preview.cpp',collect.preview_variant)):
        if (FIRMWARE/'generated'/name).read_text()!=convert((camera.APPLICATION/'src'/name).read_text()):
            raise common.SetupError('Collector source transformation changed')
    if (FIRMWARE/'generated/platform.cpp').read_text()!=camera.platform_port((ROOT/'firmware/smoke/src/platform.cpp').read_text()):
        raise common.SetupError('Collector platform transformation changed')
    compiled=[]
    entries=json.loads(commands.read_text())
    if not isinstance(entries,list):raise common.SetupError('Expected compilation-command array')
    for entry in entries:
        p=Path(entry['file']);compiled.append((p if p.is_absolute() else Path(entry['directory'])/p).resolve())
    for name in ('main.cpp','preview.cpp','platform.cpp'):
        if compiled.count((FIRMWARE/'generated'/name).resolve())!=1:
            raise common.SetupError('Expected exactly one generated collector '+name)
    if (any((camera.APPLICATION/'src'/n).resolve() in compiled for n in ('main.cpp','preview.cpp')) or
            compiled.count((camera.APPLICATION/'src/frame_export.cpp').resolve())!=1):
        raise common.SetupError('Wrong collector main/preview/exporter compiled')
    prerequisite=accepted.check_build(image)
    baseline=reports.read_json(camera.image.WORKSPACE/'manifest.json')
    if (meta['stored_image_build_manifest_sha256']!=prerequisite['build_manifest_sha256'] or
            any(meta[n]!=baseline[n] for n in ('model_sha256','reference_hashes','driver_revision','comparison'))):
        raise common.SetupError('Stored-image collector self-test prerequisite changed')
    if common.run(['git','rev-parse','HEAD'],cwd=camera.smoke.VENDOR,capture=True)!=meta['vendor_revision']:
        raise common.SetupError('Viewfinder revision changed')
    pack=common.find_cmsis().parents[2]/'AlifSemiconductor/Ensemble/2.2.0'
    linker=pack/'Device/core/rtss_hp/linker/linker_gnu_mram.ld.src';isp=pack/'libs/isp/libisp_gcc.a'
    pack_paths={linker,isp}|{p for p in compiled if p.is_relative_to(pack)}
    for folder in ('Alif_CMSIS/Include','libs/isp/inc','Device/core/common/include','Device/soc/AE822FA0E5597/include'):
        pack_paths.update((pack/folder).rglob('*.h'))
    accepted.identities(meta['pack_input_sha256'],{str(p.relative_to(pack)) for p in pack_paths},pack)
    if common.sha256(linker)!=meta['sdk_linker_sha256'] or common.sha256(isp)!=meta['isp_library_sha256']:
        raise common.SetupError('SDK linker/ISP changed')
    compiler=common.find_toolchain(None)
    for p in (elf,isp): camera.smoke.verify_arm_abi(common.run([compiler/'arm-none-eabi-readelf','-A',p],capture=True))
    symbols=camera.smoke.parse_symbols(common.run([compiler/'arm-none-eabi-nm','-S','-C','--defined-only',elf],capture=True))
    data=binary.read_bytes();layout=camera.verify_layout(symbols,data,baseline['layout']['reference_model_pte']['bytes'])
    if layout!=meta['layout']: raise common.SetupError('Collector memory/vector/startup layout changed')
    original=prerequisite['binary'].read_bytes()
    for name in ('reference_model_pte','reference_input','reference_logits'):
        address,size,_=symbols[name];offset=address-PROFILE.mram_base
        old=int(baseline['layout'][name]['address'],16)-PROFILE.mram_base
        if data[offset:offset+size]!=original[old:old+size]: raise common.SetupError('Embedded self-test changed: '+name)
    return dict(binary=binary,binary_sha256=meta['bin_sha256'],binary_bytes=len(data),
                build_manifest_sha256=common.sha256(path),image=str(image.resolve()))


def helper_identities():
    names=('camera_collect_firmware.py','prepare_camera_collect.py','program_camera_collect.py',
           'collect_camera.py','camera_inference_firmware.py','prepare_camera_inference.py','program_camera_inference.py',
           'prepare_stored_image.py','program_stored_image.py','stored_image_firmware.py','program_smoke.py',
           'smoke_firmware.py','inference.py','memory_preflight.py','firmware_layouts.py')
    return {n:common.sha256(ROOT/'tools'/n) for n in names}


def print_review(receipt,stage):
    print(f'CONTINUOUS DATA COLLECTION / NOT ASL RECOGNITION: {stage}')
    print(f"Image: {IMAGE_NAME}; {receipt['binary_bytes']} bytes; SHA-256 {receipt['binary_sha256']}")
    print('Replaces the current HP application; live v7 and original capture packages are retained separately.')
    print('One-way 128x128 full-view RGB; CRC checked; 2.5s preview + ~4.3s transfer per cycle; no UART RX.')
    print('LCD upright, camera/ML pixels unrotated. Stored-image self-test retained. Hardware acceptance pending.')
    print(f"Receipt: {stage/'receipt.json'}; preparation/review only; no serial access.")


def package_engine():
    spec=importlib.util.spec_from_file_location('tools._collect_package',capture.__file__)
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.FIRMWARE,engine.PROGRAM=FIRMWARE,PROGRAM
    engine.TARGET,engine.IMAGE_NAME,engine.CONFIG_NAME=TARGET,IMAGE_NAME,CONFIG_NAME
    engine.CRITICAL=(shared.CRITICAL_FILES-{'build/images/handgpt-smoke.bin'})|{f'build/images/{IMAGE_NAME}'}
    engine.check_build,engine.helper_identities,engine.print_review=check_build,helper_identities,print_review
    return engine


def prepare(setools,image,hp_only_board=False):
    root=Path(setools).expanduser().resolve(strict=True)
    if root.is_relative_to(common.WORK.resolve()) or common.WORK.resolve().is_relative_to(root):
        raise common.SetupError('Use installed SETOOLS outside private staging')
    return package_engine().prepare(root,Path(image).resolve(strict=True),hp_only_board)


def load_prepared(): return package_engine().load_prepared()


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('prepare',allow_abbrev=False)
    create.add_argument('--setools-root',required=True)
    create.add_argument('--image',type=Path,default=ROOT/'ml/reference/images/cat.jpg')
    create.add_argument('--hp-only-board',action='store_true')
    commands.add_parser('review',allow_abbrev=False)
    args=parser.parse_args()
    with (common.WORK/'command.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.command=='prepare':prepare(args.setools_root,args.image,args.hp_only_board)
        else:print_review(*load_prepared())


if __name__=='__main__':main()
