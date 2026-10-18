#!/usr/bin/env python3
"""Local, staged ExecuTorch export/runtime setup. Never programs a board."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".work" / "inference"
SOURCE = WORK / "executorch"
VENV = WORK / "venv"
PYTHON = VENV / "bin" / "python"
REVISION = "40d94b6d62a195a2f46b2baa20383fa4af27f7d4"
TOSA_TAG = "v2025.11.0"
TARGET = "ethos-u85-256"
REQUIREMENTS = ROOT / "ml" / "reference" / "requirements-host.txt"


class SetupError(RuntimeError):
    pass


def run(args, *, cwd=ROOT, env=None, capture=False):
    args = [str(arg) for arg in args]
    print("+ " + shlex.join(args), flush=True)
    result = subprocess.run(
        args, cwd=cwd, env=env, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else None


def build_environment(jobs=1, toolchain=None):
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "CMAKE_ARGS", "CMAKE_BUILD_ARGS",
                 "CC", "CXX", "AR", "AS", "CFLAGS", "CXXFLAGS", "LDFLAGS",
                 "CMAKE_TOOLCHAIN_FILE", "CMAKE_PREFIX_PATH"):
        env.pop(name, None)
    paths = [str(VENV / "bin")]
    if toolchain:
        paths.append(str(toolchain))
    paths.append(env.get("PATH", ""))
    env.update({
        "PATH": os.pathsep.join(paths),
        "VIRTUAL_ENV": str(VENV),
        "PYTHONNOUSERSITE": "1",
        "PIP_REQUIRE_VIRTUALENV": "true",
        "PIP_CACHE_DIR": str(WORK / "cache" / "pip"),
        "UV_CACHE_DIR": str(WORK / "cache" / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(WORK / "python"),
        "TORCH_HOME": str(WORK / "cache" / "torch"),
        "TMPDIR": str(WORK / "tmp"),
        "CMAKE_BUILD_PARALLEL_LEVEL": str(jobs),
        "MAX_JOBS": str(jobs),
        "OMP_NUM_THREADS": str(jobs),
        "CMAKE_POLICY_VERSION_MINIMUM": "3.5",
    })
    return env


def prepare_work():
    (WORK / "tmp").mkdir(parents=True, exist_ok=True)


def check_space(minimum_gib):
    free = shutil.disk_usage(ROOT).free / (1024 ** 3)
    if free < minimum_gib:
        raise SetupError(f"Only {free:.1f} GiB free; need at least {minimum_gib} GiB.")


def require_source():
    if not (SOURCE / ".git").is_dir():
        raise SetupError("Run bootstrap first; the pinned source checkout is missing.")
    actual = run(["git", "rev-parse", "HEAD"], cwd=SOURCE, capture=True)
    if actual != REVISION:
        raise SetupError(f"Unexpected ExecuTorch revision {actual}; expected {REVISION}. No reset performed.")


def require_python():
    if not PYTHON.exists():
        raise SetupError("Run bootstrap first; the isolated Python environment is missing.")


def pip_install(packages, env, *, extra=()):
    run([PYTHON, "-m", "pip", "install", "-c", REQUIREMENTS, *extra, *packages], env=env)


def host_compiler_compatibility(env):
    """GCC 16 warns on FlatCC's deliberately non-NUL character lookup table."""
    description = run(["cc", "--version"], env=env, capture=True)
    if "Free Software Foundation" in description:
        version = run(["cc", "-dumpfullversion"], env=env, capture=True)
        if int(version.split(".")[0]) >= 16:
            env["CFLAGS"] = (env.get("CFLAGS", "") + " -Wno-error=unterminated-string-initialization").strip()


def record_environment(env):
    freeze = run([PYTHON, "-m", "pip", "freeze"], env=env, capture=True)
    (WORK / "environment.freeze.txt").write_text(freeze + "\n")
    (WORK / "environment.json").write_text(json.dumps({
        "executorch_revision": REVISION,
        "python": run([PYTHON, "--version"], env=env, capture=True),
        "host": platform.platform(),
        "target": TARGET,
        "requirements_sha256": sha256(REQUIREMENTS),
        "host_c_compiler": run(["cc", "--version"], env=env, capture=True).splitlines()[0],
        "host_cflags": env.get("CFLAGS", ""),
        "host_cmake_args": env.get("CMAKE_ARGS", ""),
    }, indent=2) + "\n")


def bootstrap(args):
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "aarch64"):
        raise SetupError("This workflow currently supports x86_64/aarch64 Linux only.")
    for command in ("git", "uv", "cmake", "ninja", "c++"):
        if not shutil.which(command):
            raise SetupError(f"Missing host tool: {command}. Install it explicitly before continuing.")
    check_space(15)
    prepare_work()
    env = build_environment(args.jobs)
    if not SOURCE.exists():
        run(["git", "init", SOURCE])
        run(["git", "remote", "add", "origin", "https://github.com/pytorch/executorch.git"], cwd=SOURCE)
        run(["git", "fetch", "--depth=1", "origin", REVISION], cwd=SOURCE)
        run(["git", "switch", "--detach", "FETCH_HEAD"], cwd=SOURCE)
    require_source()
    run(["git", "submodule", "update", "--init", "--recursive", "--depth=1", "--jobs=1"], cwd=SOURCE)
    if not VENV.exists():
        run(["uv", "venv", "--seed", "--python", "3.10", VENV], env=env)
    require_python()
    version = run([PYTHON, "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"], env=env, capture=True)
    if version != "3.10":
        raise SetupError(f"Expected an isolated Python 3.10 environment, found {version}.")
    pip_install(["torch==2.10.0", "torchvision==0.25.0"], env,
                extra=["--index-url", "https://download.pytorch.org/whl/cpu"])
    pip_install(["-r", REQUIREMENTS], env)
    # Keep the portable host runtime for later numerical checks; omit unrelated backends.
    env["CMAKE_ARGS"] = " ".join([
        "-DEXECUTORCH_BUILD_XNNPACK=OFF",
        "-DEXECUTORCH_BUILD_COREML=OFF",
        "-DEXECUTORCH_BUILD_EXTENSION_LLM_RUNNER=OFF",
        "-DEXECUTORCH_BUILD_KERNELS_LLM_AOT=OFF",
        "-DEXECUTORCH_BUILD_KERNELS_LLM=OFF",
        "-DEXECUTORCH_BUILD_KERNELS_OPTIMIZED=OFF",
        "-DEXECUTORCH_BUILD_EXTENSION_TRAINING=OFF",
        "-DEXECUTORCH_BUILD_EXTENSION_LLM=OFF",
    ])
    host_compiler_compatibility(env)
    pip_install(["--no-build-isolation", "-e", SOURCE], env)
    run([PYTHON, "-m", "pip", "check"], env=env)
    record_environment(env)
    print("Host installation complete. Next: arm-tools.")


def arm_tools(args):
    require_source()
    require_python()
    check_space(5)
    prepare_work()
    env = build_environment(args.jobs)
    tosa = WORK / "tosa-tools"
    if not tosa.exists():
        run(["git", "clone", "--depth=1", "--branch", TOSA_TAG,
             "https://git.gitlab.arm.com/tosa/tosa-tools.git", tosa])
    tag_revision = run(["git", "rev-parse", f"{TOSA_TAG}^{{commit}}"], cwd=tosa, capture=True)
    head = run(["git", "rev-parse", "HEAD"], cwd=tosa, capture=True)
    if head != tag_revision:
        raise SetupError("TOSA checkout differs from its pinned tag; no checkout/reset performed.")
    run(["git", "submodule", "update", "--init", "--recursive", "--depth=1", "--jobs=1"], cwd=tosa)
    # Upstream builds serialization with pybind11<2.14. Do not run its FVP/EULA setup.
    pip_install(["--no-build-isolation",
                 "--config-settings=build-dir=" + str(WORK / "tosa-serialization-build"),
                 "--config-settings=cmake.define.BUILD_TESTS=OFF",
                 tosa / "serialization"], env)
    run([PYTHON, "-c", "import tosa_serializer, ethosu.vela; print('TOSA serialization and Vela import successfully')"], env=env)
    (WORK / "tosa-revision.txt").write_text(head + "\n")
    record_environment(env)
    print("Arm export tooling complete. Next: export --model smoke.")


def export_model(args):
    require_source()
    require_python()
    if args.model == "mv2" and not args.image:
        raise SetupError("MobileNet export requires --image /path/to/a/local/test-image.jpg for reproducible calibration/reference input.")
    check_space(2)
    prepare_work()
    env = build_environment(args.jobs)
    model = ROOT / "ml" / "reference" / ("smoke_model.py" if args.model == "smoke" else "mv2_model.py")
    output = WORK / "models" / args.model
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").unlink(missing_ok=True)
    if args.model == "mv2":
        image = Path(args.image).expanduser().resolve(strict=True)
        run([PYTHON, ROOT / "tools" / "prepare_reference_image.py", image,
             "--output", output / "reference"], env=env)
        env["HANDGPT_REFERENCE_INPUT"] = str(output / "reference" / "input.npy")
    pte = output / f"{args.model}_ethosu85_256.pte"
    run([PYTHON, "-m", "examples.arm.aot_arm_compiler", "-m", model,
         "-q", "-d", "-t", TARGET, "--memory_mode", "Sram_Only",
         "-i", output / "intermediates", "-o", pte], cwd=SOURCE, env=env)
    if not pte.is_file() or pte.stat().st_size == 0:
        raise SetupError("Export command finished without a nonempty .pte artifact.")
    delegation = output / "intermediates" / "delegation_info.txt"
    if not delegation.is_file():
        raise SetupError("Missing delegation report; inspect the export before using its output.")
    metadata = output / "program_metadata.json"
    run([PYTHON, ROOT / "tools" / "inspect_reference_pte.py", pte,
         "--model", args.model, "--output", metadata], env=env)
    emit_model_header(pte, output / "reference_model_pte.h")
    manifest = {
        "model": args.model, "target": TARGET, "memory_mode": "Sram_Only",
        "executorch_revision": REVISION, "pte_sha256": sha256(pte),
        "pte_bytes": pte.stat().st_size,
        "delegation_report": str(delegation.relative_to(WORK)),
        "metadata_sha256": sha256(metadata),
        "hardware_verified": False,
    }
    if args.model == "mv2":
        # Bind the reference input/golden outputs used during this export to the
        # same manifest as the PTE. Preflight must not accept mixed references.
        manifest["reference_sha256"] = sha256(output / "reference" / "reference.json")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Exported {pte}. Inspect {delegation}; export success is NOT hardware validation.")


def emit_model_header(pte, destination):
    data = Path(pte).read_bytes()
    if not data:
        raise SetupError("Cannot embed an empty model.")
    with Path(destination).open("w") as stream:
        stream.write("#pragma once\n#include <stdint.h>\n\n")
        stream.write("alignas(16) static const uint8_t reference_model_pte[] = {\n")
        for offset in range(0, len(data), 16):
            stream.write("    " + ", ".join(f"0x{value:02x}" for value in data[offset:offset + 16]) + ",\n")
        stream.write("};\n")
        stream.write(f"static const unsigned int reference_model_pte_len = {len(data)};\n")


def find_toolchain(explicit=None):
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    else:
        on_path = shutil.which("arm-none-eabi-gcc")
        if on_path:
            candidates.append(Path(on_path).resolve().parent)
        candidates.extend(sorted((Path.home() / ".vcpkg" / "artifacts").glob(
            "*/compilers.arm.arm.none.eabi.gcc/13.3.1/bin")))
    for candidate in candidates:
        if all((candidate / name).is_file() for name in ("arm-none-eabi-gcc", "arm-none-eabi-g++")):
            return candidate
    raise SetupError("Arm GNU tools not found. Pass --toolchain-bin /path/to/13.3.1/bin; no toolchain is downloaded automatically.")


def find_cmsis(explicit=None):
    root = Path(explicit).expanduser().resolve() if explicit else Path(
        os.environ.get("CMSIS_PACK_ROOT", str(Path.home() / ".cache" / "arm" / "packs"))
    ) / "ARM" / "CMSIS" / "6.1.0"
    if not (root / "CMSIS" / "Core" / "Include" / "cmsis_compiler.h").is_file():
        raise SetupError("CMSIS 6.1.0 headers missing; pass --cmsis-root /path/to/ARM/CMSIS/6.1.0.")
    return root


def verify_arm_archive(library, toolchain):
    headers = run([toolchain / "arm-none-eabi-readelf", "-h", library], capture=True)
    machines = {
        line.split(":", 1)[1].strip()
        for line in headers.splitlines() if line.strip().startswith("Machine:")
    }
    if machines != {"ARM"}:
        raise SetupError(f"Expected ARM objects in {library.name}, found {sorted(machines)}.")


def runtime(args):
    require_source()
    require_python()
    check_space(5)
    prepare_work()
    toolchain = find_toolchain(args.toolchain_bin)
    cmsis = find_cmsis(args.cmsis_root)
    env = build_environment(args.jobs, toolchain)
    host_compiler_compatibility(env)
    compiler = run([toolchain / "arm-none-eabi-gcc", "-dumpfullversion"], env=env, capture=True)
    if compiler != "13.3.1":
        raise SetupError(f"Expected GCC 13.3.1 to match the camera firmware, found {compiler}.")
    build = WORK / "runtime-cortex-m55"
    (build / "manifest.json").unlink(missing_ok=True)
    run(["cmake", "--preset", "arm-baremetal", "-S", SOURCE, "-B", build, "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release", "-DTARGET_CPU=cortex-m55",
         "-DCMAKE_C_FLAGS=",
         "-DPYTHON_EXECUTABLE=" + str(PYTHON),
         "-DPython3_EXECUTABLE=" + str(PYTHON)], cwd=SOURCE, env=env)
    run(["cmake", "--build", build, "--target", "install", "--parallel", args.jobs], env=env)
    # Upstream's runtime preset only builds the delegate against driver headers.
    # Build the SAME source revision's driver separately, explicitly targeting U85.
    driver = WORK / "driver-cortex-m55"
    run(["cmake", "-S", SOURCE / "backends" / "arm" / "third-party" / "ethos-u-core-driver",
         "-B", driver, "-G", "Ninja",
         "-DCMAKE_TOOLCHAIN_FILE=" + str(SOURCE / "examples" / "arm" / "ethos-u-setup" / "arm-none-eabi-gcc.cmake"),
         "-DCMAKE_BUILD_TYPE=Release", "-DTARGET_CPU=cortex-m55",
         "-DCMAKE_C_FLAGS=",
         "-DCMAKE_INSTALL_PREFIX=" + str(build),
         "-DCMSIS_PATH=" + str(cmsis), "-DETHOSU_TARGET_NPU_CONFIG=" + TARGET], env=env)
    run([
        "cmake", "--build", driver,
        "--target", "ethosu_core_driver", 
        "--parallel", args.jobs
        ], env=env)

    driver_source = (
            SOURCE / "backends" / "arm" / "third-party" / "ethos-u-core-driver"
            )
    archive = driver / "libethosu_core_driver.a"
    headers = [
            driver_source / "include" / name
            for name in ("ethosu_driver.h", "ethosu_types.h", "pmu_ethosu.h")
            ]

    #Validate everything before packaging
    for path in [archive, *headers]:
        if not path.is_file():
            raise SetupError(f"Missing driver artifact: {path}")

    (build / "lib").mkdir(parents=True, exist_ok=True)
    (build / "include").mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, build / "lib" / archive.name)
    for header in headers:
        shutil.copy2(header, build / "include" / header.name)

    libraries = sorted((build / "lib").glob("*.a"))
    required = {"libexecutorch.a", "libexecutorch_core.a", "libexecutorch_delegate_ethos_u.a", "libethosu_core_driver.a"}
    missing = required - {lib.name for lib in libraries}
    if missing:
        raise SetupError("Runtime install is missing: " + ", ".join(sorted(missing)))
    if not (build / "include" / "executorch" / "runtime" / "executor" / "program.h").is_file():
        raise SetupError("Installed runtime headers are missing.")
    for library in libraries:
        verify_arm_archive(library, toolchain)
    manifest = {
        "executorch_revision": REVISION, "cpu": "cortex-m55", "compiler": compiler,
        "toolchain_bin": str(toolchain), "cmsis_root": str(cmsis), "npu": TARGET,
        "driver_revision": run(["git", "rev-parse", "HEAD"], cwd=SOURCE / "backends" / "arm" / "third-party" / "ethos-u-core-driver", capture=True),
        "libraries": {lib.name: sha256(lib) for lib in libraries},
        "hardware_verified": False,
    }
    (build / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Runtime installed in {build}. This has not been linked into board firmware.")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def doctor(args):
    print(f"Host: {platform.system()} {platform.machine()}")
    print(f"Free disk: {shutil.disk_usage(ROOT).free / (1024 ** 3):.1f} GiB")
    print(f"Managed workspace: {WORK}")
    print(f"ExecuTorch pin: {REVISION}")
    for name in ("git", "uv", "cmake", "ninja", "c++"):
        print(f"{name}: {shutil.which(name) or 'MISSING'}")
    try:
        print(f"Arm compiler directory: {find_toolchain()}")
    except SetupError as error:
        print(error)
    try:
        print(f"CMSIS header root: {find_cmsis()}")
    except SetupError as error:
        print(error)
    print(f"Managed Python: {'present' if PYTHON.exists() else 'not bootstrapped'}")
    print("No board connection, cloud resources, FVP installation, or flashing commands are used.")


def positive_jobs(value):
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("jobs must be at least 1")
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, function in (("doctor", doctor), ("bootstrap", bootstrap),
                           ("arm-tools", arm_tools), ("export", export_model),
                           ("runtime", runtime)):
        command = commands.add_parser(name)
        command.add_argument("--jobs", type=positive_jobs, default=1)
        command.set_defaults(function=function)
        if name == "export":
            command.add_argument("--model", choices=("smoke", "mv2"), default="smoke")
            command.add_argument("--image", help="Local image used for MobileNet reference and calibration")
        if name == "runtime":
            command.add_argument("--toolchain-bin")
            command.add_argument("--cmsis-root")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            args.function(args)
        else:
            prepare_work()
            with (WORK / "command.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SetupError("Another inference stage is running in this workspace; wait for it to finish.")
                args.function(args)
    except (SetupError, subprocess.CalledProcessError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
