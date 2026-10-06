"""Offline checks for passing scene frames to the Pi matrix binding."""

from argparse import Namespace
import sys
import types
import unittest
from unittest.mock import patch

from PIL import Image

from hardware_output import MatrixOutput
from headless_scene import HeadlessScene


class FakeCanvas:
    def __init__(self):
        self.image = None

    def SetImage(self, image):
        self.image = image.copy()


class FakeMatrix:
    def __init__(self, *, options):
        self.options = options
        self.canvas = FakeCanvas()
        self.cleared = False

    def CreateFrameCanvas(self):
        return self.canvas

    def SwapOnVSync(self, canvas):
        assert canvas is self.canvas
        return canvas

    def Clear(self):
        self.cleared = True


class HardwareOutputTests(unittest.TestCase):
    def test_scene_frame_reaches_matrix_unchanged(self):
        fake_binding = types.ModuleType("rgbmatrix")
        fake_binding.RGBMatrixOptions = type("RGBMatrixOptions", (), {})
        fake_binding.RGBMatrix = FakeMatrix
        with patch.dict(sys.modules, {"rgbmatrix": fake_binding}):
            output = MatrixOutput(brightness=67, gpio_slowdown=3)
        options = output.matrix.options
        self.assertEqual((options.rows, options.cols, options.chain_length, options.parallel), (64, 64, 1, 1))
        self.assertEqual((options.hardware_mapping, options.pwm_lsb_nanoseconds), ("regular", 50))
        self.assertEqual((options.brightness, options.gpio_slowdown), (67, 3))

        args = Namespace(scene="circle", weather_demo=None)
        frame, _ = HeadlessScene(args).frame()
        output.show(frame)
        self.assertEqual(output.matrix.canvas.image.tobytes(), frame.tobytes())
        self.assertEqual(output.matrix.canvas.brightness, 67)
        output.set_brightness(42)
        self.assertEqual(output.matrix.brightness, 42)
        self.assertEqual(output.matrix.canvas.brightness, 42)
        with self.assertRaises(ValueError):
            output.show(Image.new("RGB", (32, 32)))
        output.close()
        self.assertTrue(output.matrix.cleared)

    def test_headless_offline_scenes_are_panel_sized(self):
        base = dict(
            no_numbers=False, no_ticks=False, theme="light", weather_demo="rain",
            weather_hour=23, no_progress=False, spotify_style="vinyl-spin",
            no_drift_cover=False, drift_blackness=35,
        )
        for name in ("clock", "moon", "eyes", "walker", "weather", "spotify", "kintsugi"):
            with self.subTest(scene=name):
                frame, _ = HeadlessScene(Namespace(scene=name, **base)).frame()
                self.assertEqual((frame.mode, frame.size), ("RGB", (64, 64)))


if __name__ == "__main__":
    unittest.main()
