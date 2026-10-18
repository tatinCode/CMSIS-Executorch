from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from tools import camera_collect_firmware as collect, camera_inference_firmware as camera
from tools.inference import ROOT,SetupError


class CollectBuildTests(unittest.TestCase):
    def test_private_generator_keeps_capture_globals_and_template_unchanged(self):
        before=(camera.WORKSPACE,camera.configure,camera.CAPTURE.copy())
        generator=collect.private_generator()
        self.assertNotEqual(generator.WORKSPACE,camera.WORKSPACE)
        self.assertEqual(before,(camera.WORKSPACE,camera.configure,camera.CAPTURE))
        text=(camera.APPLICATION/'src/main.cpp').read_text()
        variant=collect.main_variant(text)
        self.assertIn('COLLECT READY v=5',variant)
        self.assertNotIn('CAPTURE READY v=4',variant)
        self.assertIn('if (!camera_export_frame()) { fail(423); }',variant)
        self.assertEqual(variant.count('run_stored_image()'),2) # declaration + one boot call
        with self.assertRaises(SetupError):collect.main_variant(variant)
        preview=collect.preview_variant((camera.APPLICATION/'src/preview.cpp').read_text())
        self.assertLess(preview.index('draw_collection_panel'),preview.index('rotate_lcd180'))
        self.assertLess(preview.index('rotate_lcd180'),preview.index('clean(g_preview_lcd[next]'))

    @unittest.skipUnless(shutil.which('c++'),'Host compiler required')
    def test_collection_caption_bounds_and_upright_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'display'
            subprocess.run(['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror',
                '-I',str(collect.HEADER.parent),'-I',str(collect.FONT.parent),
                str(ROOT/'tests/native/collect_display_test.cpp'),'-o',str(exe)],check=True,capture_output=True)
            subprocess.run([str(exe)],check=True,capture_output=True)

    @unittest.skipUnless(shutil.which('c++'),'Host compiler required')
    def test_native_boot_gates_continuous_export_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'main.cpp').write_text(collect.main_variant((camera.APPLICATION/'src/main.cpp').read_text()))
            (root/'image_config.h').write_text('#define IMAGE_MODEL_SHA256 "model"\n#define IMAGE_INPUT_SHA256 "input"\n')
            flags=['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-I',tmp]
            for directory in (collect.HEADER.parent,collect.FONT.parent,ROOT/'firmware/camera_inference/include',ROOT/'firmware/smoke/include'):
                flags+=['-I',str(directory)]
            subprocess.run([*flags,'-Dmain=collect_main','-c',str(root/'main.cpp'),'-o',str(root/'main.o')],check=True,capture_output=True)
            subprocess.run([*flags,str(root/'main.o'),str(ROOT/'tests/native/collect_main_test.cpp'),'-o',str(root/'test')],check=True,capture_output=True)
            for scenario in ('success','board-error','selftest-error','export-error'):
                result=subprocess.run([str(root/'test'),scenario],check=True,capture_output=True,timeout=10)
                self.assertEqual(b'COLLECT READY' in result.stdout,scenario in ('success','export-error'))
                if scenario=='success':
                    self.assertEqual(result.stdout.count(b'HGCONT BEGIN'),4)
                    self.assertEqual(result.stdout.count(b'HGCONT END'),3)
