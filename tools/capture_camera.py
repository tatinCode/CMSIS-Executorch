"""Receive one CRC-checked E8 frame over application UART; never programs firmware."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import uuid
import zlib

from tools.program_stored_image import port_identity


WIDTH = HEIGHT = 480
FRAME_BYTES = WIDTH * HEIGHT * 3
LABELS = ("A", "B", "C", "L", "Y", "NO_HAND", "UNKNOWN")
HEADER = re.compile(rb"HGFRAME ([0-9]) ([0-9]{1,3}) ([0-9]{1,3}) RGB888P ([0-9]{1,6}) ([0-9]{1,10}) ([0-9a-f]{8})\n")
VERSION = re.compile(rb"CAPTURE (?:READY|IDLE) v=([0-9]+);")
PACKETS = {(1, 480, 480, 691200): 3, (2, 128, 128, 49152): 4}


def read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            raise ValueError("Truncated camera frame")
        data.extend(chunk)
    return bytes(data)


def read_packet(stream):
    header = stream.readline(160)
    match = HEADER.fullmatch(header)
    if not match:
        raise ValueError(f"Invalid camera-frame header: {header!r}")
    wire, width, height, size, frame = (int(match[i]) for i in range(1, 6))
    if (wire, width, height, size) not in PACKETS:
        raise ValueError("Unsupported camera-frame version/dimensions/length")
    checksum = int(match[6], 16)
    if not 0 < frame <= 0xffffffff:
        raise ValueError("Invalid camera frame number")
    payload = read_exact(stream, size)
    trailer = f"\nHGFRAME END {frame} {checksum:08x}\n".encode()
    if read_exact(stream, len(trailer)) != trailer:
        raise ValueError("Camera-frame trailer mismatch")
    if zlib.crc32(payload) != checksum:
        raise ValueError("Camera-frame CRC32 mismatch; do not use this image")
    return payload, {"frame": frame, "crc32": f"{checksum:08x}", "format": "RGB888P",
                     "width": width, "height": height, "wire_version": wire,
                     "source_width": 480, "source_height": 480, "crop": "none",
                     "resize": "none" if wire == 1 else "PIL_bilinear_22bit"}


def as_image(payload, width=WIDTH, height=HEIGHT):
    from PIL import Image
    if (width, height) not in ((480, 480), (128, 128)) or len(payload) != width * height * 3:
        raise ValueError("Expected one 480x480 or 128x128 planar RGB888 frame")
    plane = width * height
    return Image.merge("RGB", tuple(Image.frombytes("L", (width, height), payload[i * plane:(i + 1) * plane])
                                    for i in range(3)))


class SerialStream:
    """Bounded reads with one buffered reader, preserving bytes after a header."""

    def __init__(self, fd, deadline):
        self.fd, self.deadline, self.pending = fd, deadline, bytearray()
        self.received = 0
        self.recent = bytearray()

    def read(self, count):
        if count <= 0:
            return b""
        if self.pending:
            result = bytes(self.pending[:count])
            del self.pending[:count]
            return result
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise TimeoutError("UART deadline expired")
            try:
                chunk = os.read(self.fd, count)
            except BlockingIOError:
                continue
            if not chunk:
                raise OSError("UART disconnected")
            self.received += len(chunk)
            self.recent.extend(chunk)
            del self.recent[:-2048]
            return chunk

    def readline(self, limit):
        result = bytearray()
        while len(result) < limit:
            chunk = self.read(min(4096, limit - len(result)))
            end = chunk.find(b"\n")
            if end >= 0:
                result.extend(chunk[:end + 1])
                self.pending[:0] = chunk[end + 1:]
                break
            result.extend(chunk)
        return bytes(result)


def receive(fd, ready_timeout, timeout):
    stream = SerialStream(fd, time.monotonic() + ready_timeout)
    log = bytearray()
    phase = "waiting for CAPTURE READY v=3/v=4 (press board RESET after LISTENING)"
    try:
        # Both boot-export revisions are one-way: never write to fd.
        while True:
            line = stream.readline(512)
            log.extend(line)
            if len(log) > 65536:
                raise ValueError("No boot-export marker within 64 KiB of UART output")
            if b"COEXIST FAIL" in line:
                raise ValueError("Firmware reported COEXIST FAIL")
            if line.startswith((b"CAPTURE READY v=", b"CAPTURE IDLE v=")):
                version = VERSION.match(line)
                if not version or int(version[1]) not in (3, 4):
                    raise ValueError("Wrong capture firmware: use a reviewed v3 or v4 boot-export package")
                if line.startswith(b"CAPTURE IDLE"):
                    # Already booted: remain armed until the user resets.
                    continue
                protocol = int(version[1])
                break
        stream.deadline = time.monotonic() + timeout
        phase = f"waiting for HGFRAME header after v{protocol} ready marker"
        while True:
            line = stream.readline(512)
            if line.startswith(b"HGFRAME "):
                log.extend(line)
                stream.pending[:0] = line
                phase = "receiving frame payload/trailer and checking CRC32"
                payload, metadata = read_packet(stream)
                if PACKETS[(metadata["wire_version"], metadata["width"], metadata["height"], len(payload))] != protocol:
                    raise ValueError("Capture boot marker and packet profile disagree")
                return payload, dict(metadata, capture_protocol=protocol), bytes(log)
            log.extend(line)
            if (b"COEXIST FAIL" in line or b"CAPTURE EXPORT LOST" in line or
                    b"CAPTURE IDLE" in line or len(log) > 65536):
                raise ValueError("Frame export failed or was missed; reset the board to retry")
    except (OSError, ValueError) as error:
        details = (f"{phase}: {error}; received={stream.received} bytes; "
                   f"recent UART={bytes(stream.recent)!r}")
        if isinstance(error, TimeoutError):
            raise TimeoutError(details) from error
        if isinstance(error, OSError):
            raise OSError(details) from error
        raise ValueError(details) from error


def save_capture(root, session, label, payload, metadata, log):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session) or label not in LABELS:
        raise ValueError("Invalid session or label")
    image = as_image(payload, metadata.get("width", WIDTH), metadata.get("height", HEIGHT))
    directory = root / session / label
    directory.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    base = directory / stem
    png = io.BytesIO()
    image.save(png, format="PNG")
    png_bytes = png.getvalue()
    record = dict(metadata, schema=1, session=session, label=label,
                  captured_at=datetime.now(timezone.utc).isoformat(),
                  raw_sha256=hashlib.sha256(payload).hexdigest(), png_sha256=hashlib.sha256(png_bytes).hexdigest(),
                   source=f"E8 application UART; CRC-checked boot-triggered frozen frame (v{metadata.get('capture_protocol', 3)})",
                   split="unassigned")
    # Metadata is written last: incomplete groups are not accepted captures.
    for extension, data in ((".rgb", payload), (".png", png_bytes), (".log", log),
                            (".json", (json.dumps(record, indent=2) + "\n").encode())):
        with base.with_suffix(extension).open("xb") as handle:
            handle.write(data)
    return base.with_suffix(".png")


def configure_serial(fd):
    # Apply raw mode immediately after open. Keep it on close: restoring an old
    # ECHO/baud configuration can feed the board's own log text back into RX.
    settings = termios.tcgetattr(fd)
    settings[0] = settings[1] = settings[3] = 0
    settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    settings[4] = settings[5] = termios.B115200
    settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, settings)


def checked_identity(port):
    """Validate port identity and reject an existing serial monitor."""
    identity = port_identity(port)
    fuser = shutil.which("fuser")
    if not fuser:
        raise ValueError("fuser is required to check for an existing serial monitor")
    busy = subprocess.run([fuser, port], capture_output=True)
    if busy.returncode != 1:
        raise ValueError("Serial port is in use or its ownership could not be checked; close picocom first")
    return identity


@contextmanager
def listening_port(port, identity):
    if port_identity(port) != identity:
        raise ValueError("Serial device changed while waiting")
    fd = os.open(port, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    exclusive = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(fd, termios.TIOCEXCL)
        exclusive = True
        configure_serial(fd)
        termios.tcflush(fd, termios.TCIFLUSH)
        yield fd
    finally:
        try:
            if exclusive:
                fcntl.ioctl(fd, termios.TIOCNXCL)
        finally:
            os.close(fd)


def diagnose(args):
    """Read-only listen; escape binary bytes so they cannot control a terminal."""
    identity = checked_identity(args.port)
    log = bytearray()
    total = 0
    versions = set()
    latest = None
    failed = False
    displayed = 0
    with listening_port(args.port, identity) as fd:
        print(f"Read-only listen for {args.seconds:g}s on {args.port} ({identity}); "
              "nothing is sent. Press the board reset to see a full boot.", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                break
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                raise OSError("UART disconnected")
            log.extend(chunk)
            del log[:-65536]
            markers = VERSION.findall(log)
            if markers:
                versions.update(int(marker) for marker in markers)
                latest = int(markers[-1])
            failed = failed or b"COEXIST FAIL" in log
            total += len(chunk)
            if displayed < 8192:
                excerpt = chunk[:8192 - displayed]
                print(f"UART {excerpt!r}", flush=True)
                displayed += len(excerpt)
                if displayed == 8192:
                    print("UART display capped at 8192 bytes; continuing to count received bytes.", flush=True)
    if failed:
        print("\nDIAGNOSE: firmware reported COEXIST FAIL.", file=sys.stderr)
        return 1
    elif versions:
        print(f"\nDIAGNOSE: capture protocol markers {sorted(versions)} "
              f"(latest v={latest}); {total} bytes in {args.seconds:g}s.")
    else:
        print(f"\nDIAGNOSE: no CAPTURE version marker; {total} bytes received. "
              "Power-cycle, confirm SW4 is U4, and try again.", file=sys.stderr)
    return 0


def capture(args):
    # Import/check image support before opening hardware.
    from PIL import Image  # noqa: F401
    identity = checked_identity(args.port)
    if not sys.stdin.isatty():
        raise ValueError("Capture needs an interactive terminal for pose/label confirmation")
    print(f"SW4 must be U4. Label={args.label}; session={args.session}. Position the hand using the display.")
    print("The board exports one full-view image at every boot: press Enter to arm the listener, "
          "then press the board's RESET button while holding the pose.")
    input("Press Enter to arm (v4: ~5s transfer; v3: ~60s; boot/settling extra): ")
    with listening_port(args.port, identity) as fd:
        print("LISTENING — hold the pose and press the board RESET now. "
              "No UART request is sent.", flush=True)
        payload, metadata, log = receive(fd, args.ready_timeout, args.timeout)
    metadata["port_identity"] = identity
    path = save_capture(args.output, args.session, args.label, payload, metadata, log)
    print(f"CRC verified. Saved {path}")
    print(f"Capture v{metadata['capture_protocol']}: {metadata['width']}x{metadata['height']}, full camera view; "
          f"resize={metadata['resize']}.")
    print("Check image orientation/colors and label before use. "
          "Start this tool again before resetting for the next frame.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--port", required=True)
    parser.add_argument("--label", choices=LABELS)
    parser.add_argument("--session")
    parser.add_argument("--output", type=Path, default=Path(".work/asl/camera"))
    parser.add_argument("--timeout", type=float, default=100,
                        help="Payload transfer budget after the boot-export marker")
    parser.add_argument("--ready-timeout", type=float, default=30,
                        help="How long to wait for the post-reset boot marker")
    parser.add_argument("--diagnose", action="store_true",
                        help="Read-only listen; prints board output and sends nothing")
    parser.add_argument("--seconds", type=float, default=20, help="Duration for --diagnose")
    args = parser.parse_args()
    if args.diagnose and not 1 <= args.seconds <= 300:
        parser.error("Use --seconds between 1 and 300")
    session_ok = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.session or ""))
    if not args.diagnose and (not session_ok or args.label is None or
                               not 10 <= args.timeout <= 300 or not 5 <= args.ready_timeout <= 300):
        parser.error("Use a simple session identifier, a label, a timeout between 10 and 300 seconds, "
                     "and --ready-timeout between 5 and 300 seconds")
    try:
        if args.diagnose:
            return diagnose(args)
        capture(args)
    except KeyboardInterrupt:
        print("Capture cancelled; let the board finish its transfer, or reset after rearming the listener.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
