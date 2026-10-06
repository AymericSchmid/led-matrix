"""Send the same 64 x 64 Pillow frames used by the emulator to a HUB75 panel."""

import os

from PIL import Image


MATRIX_SIZE = (64, 64)


class MatrixOutput:
    def __init__(self, *, brightness: int = 80, gpio_slowdown: int = 2):
        # Import only in hardware mode; the desktop emulator needs no Pi modules.
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions
        except ImportError as error:
            raise RuntimeError(
                "The Pi's rgbmatrix Python binding is not installed. "
                "Install rpi-rgb-led-matrix on the Pi before using --output matrix."
            ) from error

        options = RGBMatrixOptions()
        options.rows = 64
        options.cols = 64
        options.chain_length = 1
        options.parallel = 1
        options.hardware_mapping = "regular"
        options.pwm_lsb_nanoseconds = 50
        options.brightness = brightness
        options.gpio_slowdown = gpio_slowdown

        # The library needs root for setup, then can return to the account that
        # invoked sudo. Spotify's token file remains writable by that account.
        sudo_uid = os.environ.get("SUDO_UID")
        sudo_gid = os.environ.get("SUDO_GID")
        if hasattr(os, "geteuid") and os.geteuid() == 0 and sudo_uid and sudo_gid:
            import grp
            import pwd

            options.drop_priv_user = pwd.getpwuid(int(sudo_uid)).pw_name
            options.drop_priv_group = grp.getgrgid(int(sudo_gid)).gr_name

        self.matrix = RGBMatrix(options=options)
        self.canvas = self.matrix.CreateFrameCanvas()

    def show(self, frame: Image.Image) -> None:
        if frame.size != MATRIX_SIZE or frame.mode != "RGB":
            raise ValueError("The matrix expects a 64 x 64 RGB image.")
        self.canvas.SetImage(frame)
        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def close(self) -> None:
        self.matrix.Clear()
