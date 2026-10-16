"""Perceptual colormap data for the NozzleBoss viewport overlay.

matplotlib is not available inside Blender, so the lookup table is built here by
interpolating landmark colours sampled from matplotlib's 'inferno'. The
landmarks are close but not the exact 256-entry table - the gradient reads as
inferno, yet individual entries may differ in the third decimal.

To drop in exact values, replace the landmark tuple with the output of

    matplotlib.colormaps['inferno'](np.linspace(0, 1, 256))[:, :3]

using np.linspace(0, 1, 256) as the positions. All landmark values are sRGB.
"""

import numpy as np


INFERNO = (
    (0.00, (0.0015, 0.0005, 0.0139)),
    (0.05, (0.0231, 0.0192, 0.0908)),
    (0.10, (0.0656, 0.0360, 0.1790)),
    (0.15, (0.1267, 0.0429, 0.2688)),
    (0.20, (0.1960, 0.0434, 0.3531)),
    (0.25, (0.2660, 0.0510, 0.4090)),
    (0.30, (0.3370, 0.0578, 0.4288)),
    (0.35, (0.4046, 0.0771, 0.4331)),
    (0.40, (0.4720, 0.1097, 0.4281)),
    (0.45, (0.5404, 0.1332, 0.4152)),
    (0.50, (0.6086, 0.1598, 0.3932)),
    (0.55, (0.6753, 0.1869, 0.3641)),
    (0.60, (0.7355, 0.2159, 0.3302)),
    (0.65, (0.7943, 0.2504, 0.2882)),
    (0.70, (0.8458, 0.2949, 0.2440)),
    (0.75, (0.8921, 0.3479, 0.1953)),
    (0.80, (0.9298, 0.4113, 0.1453)),
    (0.85, (0.9585, 0.4844, 0.0942)),
    (0.90, (0.9752, 0.5698, 0.0451)),
    (0.95, (0.9763, 0.7594, 0.1387)),
    (1.00, (0.9884, 0.9984, 0.6449)),
)

COLORMAPS = {'INFERNO': INFERNO}


def build_lut(name='INFERNO', size=256):
    """Return a size x 3 sRGB lookup table for the named colormap."""
    landmarks = COLORMAPS[name]
    positions = np.array([p for p, _ in landmarks], dtype=np.float64)
    colours = np.array([c for _, c in landmarks], dtype=np.float64)
    t = np.linspace(0.0, 1.0, size)
    return np.stack([np.interp(t, positions, colours[:, i]) for i in range(3)], axis=1)


def srgb_to_linear(values):
    """Blender ColorRamp stops and FLOAT_COLOR attributes are linear."""
    c = np.asarray(values, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def ramp_stops(name='INFERNO', count=24):
    """(position, linear rgb) pairs for a Color Ramp node.

    Blender caps a colorband at 32 elements, so count must stay under that.
    """
    count = max(2, min(count, 32))
    lut = build_lut(name, count)
    positions = np.linspace(0.0, 1.0, count)
    linear = srgb_to_linear(lut)
    return tuple(zip(positions.tolist(), linear.tolist()))
