import io
import argparse
import json
from contextlib import nullcontext
import os
import pty
import termios
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import zlib

from tools import capture_camera as capture
from tools.inference import ROOT


def packet(payload, frame=7, version=1):
    crc = zlib.crc32(payload)
    side = 480 if version == 1 else 128
    return (f"HGFRAME {version} {side} {side} RGB888P {side * side * 3} {frame} {crc:08x}\n".encode() + payload +
            f"\nHGFRAME END {frame} {crc:08x}\n".encode())


class CaptureCameraTests(unittest.TestCase):
    def test_private_port_keeps_tx_callback_and_never_enables_rx(self):
        from tools.camera_inference_firmware import platform_port
        source = platform_port((ROOT / "firmware/smoke/src/platform.cpp").read_text())
        baseline = (ROOT / "firmware/smoke/src/platform.cpp").read_text()
        start, end = baseline.index("void uart_callback("), baseline.index("struct UartTransport")
        self.assertIn(baseline[start:end], source)
        for forbidden in ("ARM_USART_CONTROL_RX", "->Receive(", "camera_capture_command", "camera_rx_"):
            self.assertNotIn(forbidden, source)

    def test_serial_setup_disables_echo_translations_and_flow_control(self):
        master, slave = pty.openpty()
        try:
            settings = termios.tcgetattr(slave)
            settings[3] |= termios.ECHO | termios.ICANON
            termios.tcsetattr(slave, termios.TCSANOW, settings)
            capture.configure_serial(slave)
            configured = termios.tcgetattr(slave)
            self.assertEqual(configured[0], 0)
            self.assertEqual(configured[1], 0)
            self.assertEqual(configured[3], 0)
            self.assertFalse(configured[2] & termios.HUPCL)
            self.assertEqual(configured[4:6], [termios.B115200, termios.B115200])
        finally:
            os.close(slave)
            os.close(master)

    def test_binary_with_embedded_newlines_and_markers_is_length_delimited(self):
        payload = (b"\nHGFRAME END 7\x00\xff" * capture.FRAME_BYTES)[:capture.FRAME_BYTES]
        data, metadata = capture.read_packet(io.BytesIO(packet(payload)))
        self.assertEqual(data, payload)
        self.assertEqual(metadata["frame"], 7)

    def test_fast_packet_is_bounded_and_preserves_full_view_provenance(self):
        payload = bytes(range(256)) * (49152 // 256)
        data, metadata = capture.read_packet(io.BytesIO(packet(payload, version=2)))
        self.assertEqual(data, payload)
        self.assertEqual((metadata["width"], metadata["height"], metadata["wire_version"]), (128, 128, 2))
        self.assertEqual(metadata["resize"], "PIL_bilinear_22bit")
        self.assertEqual(metadata["source_width"], 480)
        self.assertEqual(metadata["crop"], "none")
        original = packet(payload, version=2)
        corrupt = bytearray(original)
        corrupt[100] ^= 1
        for broken in (bytes(corrupt), original[:-1], original.replace(b"49152", b"691200", 1),
                       original.replace(b"128 128", b"480 480", 1), original.replace(b"HGFRAME 2", b"HGFRAME 9", 1),
                       original.replace(b" 7 ", b" 0 ", 1), original.replace(b"END 7", b"END 8", 1)):
            with self.subTest(header=broken[:60]), self.assertRaises(ValueError):
                capture.read_packet(io.BytesIO(broken))

    def test_fast_boot_export_fragmentation_no_host_writes_and_profile_mismatch(self):
        for protocol in (3, 4):
            with self.subTest(protocol=protocol):
                host, device = socket.socketpair()
                payload = bytes(49152)
                wire = (f"CAPTURE IDLE v={protocol}; reset\nCAPTURE READY v={protocol}; boot\n".encode() +
                        packet(payload, version=2))
                errors = []
                def board():
                    try:
                        for offset in range(0, len(wire), 31):
                            device.sendall(wire[offset:offset + 31])
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        device.close()
                thread = threading.Thread(target=board)
                thread.start()
                try:
                    with mock.patch.object(capture.os, "write", side_effect=AssertionError("must not send")):
                        if protocol == 3:
                            with self.assertRaisesRegex(ValueError, "profile disagree"):
                                capture.receive(host.fileno(), 5, 5)
                        else:
                            data, metadata, log = capture.receive(host.fileno(), 5, 5)
                            self.assertEqual(data, payload)
                            self.assertEqual(metadata["capture_protocol"], 4)
                            self.assertIn(b"CAPTURE READY v=4", log)
                finally:
                    host.close()
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                if errors:
                    raise errors[0]

    def test_reject_bad_crc_truncation_trailer_dimensions_and_version(self):
        original = packet(bytes(capture.FRAME_BYTES))
        corrupt = bytearray(original)
        corrupt[100] ^= 1
        for broken in (bytes(corrupt), original[:-1], original[:-20], original.replace(b"END 7", b"END 8"),
                       original.replace(b"480 480", b"240 480", 1), original.replace(b"HGFRAME 1", b"HGFRAME 2", 1)):
            with self.assertRaises(ValueError):
                capture.read_packet(io.BytesIO(broken))

    def test_boot_export_preserves_buffered_payload_and_sends_nothing(self):
        host, device = socket.socketpair()
        payload = bytes(range(256)) * (capture.FRAME_BYTES // 256)
        errors = []
        def board():
            try:
                wire = b"CAPTURE IDLE v=3; reset\nCAPTURE READY v=3; boot export\n" + packet(payload)
                for offset in range(0, len(wire), 317):
                    device.sendall(wire[offset:offset + 317])
            except BaseException as error:
                errors.append(error)
            finally:
                device.close()
        thread = threading.Thread(target=board)
        thread.start()
        try:
            with mock.patch.object(capture.os, "write", side_effect=AssertionError("must not send")):
                data, metadata, log = capture.receive(host.fileno(), 5, 5)
            self.assertEqual(data, payload)
            self.assertEqual(metadata["frame"], 7)
            self.assertIn(b"CAPTURE READY", log)
            self.assertEqual(metadata["capture_protocol"], 3)
        finally:
            host.close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]

    def test_ready_timeout_includes_phase_byte_count_and_partial_line(self):
        host, device = socket.socketpair()
        try:
            device.sendall(b"PREVIEW frames=5\npartial-line")
            with self.assertRaises(TimeoutError) as raised:
                capture.receive(host.fileno(), .01, 1)
            self.assertIn("waiting for CAPTURE READY", str(raised.exception))
            self.assertIn("received=29 bytes", str(raised.exception))
            self.assertIn("partial-line", str(raised.exception))
            device.setblocking(False)
            with self.assertRaises(BlockingIOError):
                device.recv(1)
        finally:
            host.close()
            device.close()

    def test_silent_port_reports_zero_bytes(self):
        host, device = socket.socketpair()
        try:
            with self.assertRaisesRegex(TimeoutError, "received=0 bytes"):
                capture.receive(host.fileno(), .01, 1)
        finally:
            host.close()
            device.close()

    def test_wrong_firmware_failure_and_lost_export_fail_explicitly(self):
        cases = ((b"CAPTURE READY v=1; old\n", "Wrong capture firmware"),
                 (b"CAPTURE READY v=2; old\n", "Wrong capture firmware"),
                 (b"COEXIST FAIL error=209\n", "Firmware reported COEXIST FAIL"),
                 (b"CAPTURE READY v=3; boot\nCAPTURE EXPORT LOST v=3;\n", "Frame export failed"))
        for wire, message in cases:
            with self.subTest(message=message, wire=wire):
                host, device = socket.socketpair()
                try:
                    device.sendall(wire)
                    with self.assertRaisesRegex(ValueError, message):
                        capture.receive(host.fileno(), .1, .1)
                    device.setblocking(False)
                    with self.assertRaises(BlockingIOError):
                        device.recv(1)
                finally:
                    host.close()
                    device.close()

    def test_header_and_payload_timeouts_have_distinct_phases(self):
        for suffix, expected in ((b"", "waiting for HGFRAME header"),
                                 (b"HGFRAME 1 480 480 RGB888P 691200 7 00000000\nabc", "receiving frame payload")):
            host, device = socket.socketpair()
            try:
                device.sendall(b"CAPTURE READY v=3; boot\n" + suffix)
                with self.assertRaisesRegex(TimeoutError, expected):
                    capture.receive(host.fileno(), .1, .01)
            finally:
                host.close()
                device.close()

    def test_read_only_port_context_and_serial_configuration(self):
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        try:
            with mock.patch.object(capture, "port_identity", return_value={"test": True}):
                with capture.listening_port(path, {"test": True}) as fd:
                    import fcntl
                    self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
                    self.assertEqual(termios.tcgetattr(fd)[3], 0)
        finally:
            os.close(slave)
            os.close(master)

    def test_capture_workflow_arms_before_reset_and_saves_real_pty_frame(self):
        for version in (1, 2):
            with self.subTest(wire_version=version):
                self.capture_workflow(version)

    def capture_workflow(self, version):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Use the ASL environment for image checks")
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        listening = threading.Event()
        errors = []
        class Output(io.StringIO):
            def write(self, text):
                result = super().write(text)
                if "LISTENING" in text:
                    listening.set()
                return result
        output = Output()
        side = 480 if version == 1 else 128
        protocol = version + 2
        payload = bytes(side * side * 3)
        def board():
            try:
                if not listening.wait(5):
                    raise AssertionError("listener was not armed")
                wire = (f"COEXIST SELFTEST PASS checked=1000\nCAPTURE READY v={protocol}; boot\n".encode() +
                        packet(payload, version=version))
                for offset in range(0, len(wire), 317):
                    os.write(master, wire[offset:offset + 317])
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=board, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                args = argparse.Namespace(port=path, output=Path(directory), session="plumbing01", label="A",
                                          ready_timeout=5, timeout=5)
                with mock.patch.object(capture, "port_identity", return_value={"test": True}), \
                        mock.patch.object(capture.shutil, "which", return_value="fuser"), \
                        mock.patch.object(capture.subprocess, "run", return_value=argparse.Namespace(returncode=1)), \
                        mock.patch.object(capture.sys.stdin, "isatty", return_value=True), \
                        mock.patch("builtins.input", return_value=""), \
                        mock.patch.object(capture.sys, "stdout", output):
                    capture.capture(args)
                self.assertIn("CRC verified", output.getvalue())
                files = list(Path(directory).rglob("*.png"))
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].with_suffix(".rgb").read_bytes(), payload)
                with Image.open(files[0]) as image:
                    self.assertEqual(image.size, (side, side))
                record = json.loads(files[0].with_suffix(".json").read_text())
                self.assertEqual(record["capture_protocol"], protocol)
        finally:
            thread.join(timeout=5)
            os.close(slave)
            os.close(master)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]

    def test_diagnose_is_read_only_and_escapes_terminal_control_bytes(self):
        host, device = socket.socketpair()
        try:
            device.sendall(b"CAPTURE READY v=3; boot\n\x1b[31m\x00\xff")
            output = io.StringIO()
            args = argparse.Namespace(port="fake", seconds=.02)
            with mock.patch.object(capture, "checked_identity", return_value={}), \
                    mock.patch.object(capture, "listening_port", return_value=nullcontext(host.fileno())), \
                    mock.patch.object(capture.sys, "stdout", output), \
                    mock.patch.object(capture.os, "write", side_effect=AssertionError("must not send")):
                self.assertEqual(capture.diagnose(args), 0)
            self.assertIn("latest v=3", output.getvalue())
            self.assertIn("\\x1b", output.getvalue())
            self.assertNotIn("\x1b", output.getvalue())
            device.setblocking(False)
            with self.assertRaises(BlockingIOError):
                device.recv(1)
        finally:
            host.close()
            device.close()

    def test_planar_color_order_and_lossless_saved_image(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Use the ASL environment for image checks")
        plane = capture.WIDTH * capture.HEIGHT
        payload = bytes([255]) * plane + bytes([128]) * plane + bytes(plane)
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(Path(directory), "person1-session1", "A", payload, {}, b"test")
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 128, 0))
                self.assertEqual(image.getpixel((479, 479)), (255, 128, 0))
            self.assertEqual(path.with_suffix(".rgb").read_bytes(), payload)
            self.assertTrue(path.with_suffix(".json").exists())

    def test_fast_saved_image_colors_dimensions_metadata_and_invalid_lengths(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Use the ASL environment for image checks")
        plane = 128 * 128
        payload = bytes([255]) * plane + bytes([128]) * plane + bytes(plane)
        _, metadata = capture.read_packet(io.BytesIO(packet(payload, version=2)))
        metadata["capture_protocol"] = 4
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(Path(directory), "presenter01", "L", payload, metadata, b"test")
            with Image.open(path) as image:
                self.assertEqual(image.size, (128, 128))
                self.assertEqual(image.getpixel((0, 0)), (255, 128, 0))
                self.assertEqual(image.getpixel((127, 127)), (255, 128, 0))
            record = json.loads(path.with_suffix(".json").read_text())
            self.assertEqual(record["capture_protocol"], 4)
            self.assertEqual(record["resize"], "PIL_bilinear_22bit")
            self.assertIn("(v4)", record["source"])
            for bad in (payload[:-1], bytes(capture.FRAME_BYTES)):
                with self.assertRaises(ValueError):
                    capture.save_capture(Path(directory), "presenter01", "L", bad, metadata, b"")

    @unittest.skipUnless(shutil.which("c++"), "host compiler required")
    def test_firmware_wire_format_crc_and_write_failures(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Use the ASL environment for image checks")
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "export"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O2",
                            "-DTEST_WRAP_WRITES", "-Wl,--wrap=fwrite",
                            "-I", str(ROOT / "firmware/camera_inference/include"),
                            "-I", str(ROOT / "firmware/smoke/include"),
                            str(ROOT / "firmware/camera_inference/src/frame_export.cpp"),
                            str(ROOT / "tests/native/camera_export_test.cpp"), "-o", str(executable)],
                           check=True, capture_output=True)
            result = subprocess.run([executable], check=True, capture_output=True)
            payload, metadata = capture.read_packet(io.BytesIO(result.stdout))
            source = bytes((i * 17 + i // 480) & 255 for i in range(capture.FRAME_BYTES))
            image = capture.as_image(source).resize((128, 128), Image.Resampling.BILINEAR)
            self.assertEqual(payload, b"".join(channel.tobytes() for channel in image.split()))
            self.assertEqual(metadata["frame"], 7)
            self.assertEqual(metadata["width"], 128)
            for scenario in ("no-frame", "uart-error"):
                result = subprocess.run([executable, scenario], capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
            for scenario in ("write-error", "uart-during-export"):
                result = subprocess.run([executable, scenario], capture_output=True)
                self.assertEqual(result.returncode, 1)
                with self.assertRaises(ValueError):
                    capture.read_packet(io.BytesIO(result.stdout))
            with open("/dev/full", "wb") as sink:
                result = subprocess.run([executable], stdout=sink)
                self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
