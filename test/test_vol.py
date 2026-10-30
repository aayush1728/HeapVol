import math

import numpy as np
import pytest

from core.volume import compute_vol

RES = 0.1                      # 10 cm pixels
N = 400                        # 40 m x 40 m grid


def grid():
    c = (np.arange(N) + 0.5) * RES
    return np.meshgrid(c, c)


def test_cone_volume_matches_formula():
    x, y = grid()
    r = np.hypot(x - 20, y - 20)
    base = np.ma.asarray(np.full((N, N), 10.0))
    dem = base + np.clip(5 * (1 - r / 10), 0, None)          # cone: radius 10 m, height 5 m
    inside = r < 12
    res = compute_vol(base, dem, inside, RES * RES)
    assert res.fill_m3 == pytest.approx(math.pi * 10**2 * 5 / 3, rel=0.01)
    assert res.cut_m3 == 0.0 and res.net_m3 == res.fill_m3


def test_cut_is_positive_and_net_subtracts_it():
    base = np.ma.asarray(np.full((2, 2), 10.0))
    dem = np.ma.asarray(np.array([[12.0, 12.0], [9.0, 9.0]]))    # +2 m on two px, -1 m on two px
    res = compute_vol(base, dem, np.ones((2, 2), bool), 1.0)
    assert (res.fill_m3, res.cut_m3, res.net_m3) == (4.0, 2.0, 2.0)


def test_nodata_is_skipped_and_counted():
    base = np.ma.asarray(np.full((2, 2), 10.0))
    dem = np.ma.array([[12.0, -32767.0], [12.0, 12.0]], mask=[[False, True], [False, False]])
    res = compute_vol(base, dem, np.ones((2, 2), bool), 1.0)
    assert res.fill_m3 == 6.0 and res.nodata_pixels == 1 and res.valid_pixels == 3


def test_no_negative_pixels_gives_zero_cut_not_masked():
    base = np.ma.asarray(np.zeros((3, 3)))
    res = compute_vol(base, base + 1, np.ones((3, 3), bool), 1.0)
    assert res.cut_m3 == 0.0 and isinstance(res.cut_m3, float)


def test_shape_mismatch_raises():
    with pytest.raises(ValueError, match="Shape mismatch"):
        compute_vol(np.ma.zeros((3, 3)), np.ma.zeros((3, 4)), np.ones((3, 3), bool), 1.0)
