from dataclasses import dataclass

import numpy as np

@dataclass(frozen=True)
class VolResult:
    fill_m3: float          
    cut_m3: float          
    net_m3: float           
    valid_pixels: int
    nodata_pixels: int

def compute_vol(old_dem, new_dem, inside, pixel_area):
    
    if (old_dem.shape != new_dem.shape) or (old_dem.shape != inside.shape):
        raise ValueError("Shape mismatch: Dems are not of eqaul size.")
    if pixel_area <= 0:
        raise ValueError("Pixel area must be positive")

    diff = (new_dem - old_dem)[inside].compressed().astype(np.float64)

    cut  = abs(diff[diff<0]*pixel_area)
    fill = diff[diff>0]*pixel_area

    net = fill.sum() - cut.sum()

    valid = diff.size
    nodata = int(inside.sum()) - valid

    return VolResult(fill_m3 = float(fill.sum()), cut_m3 = float(cut.sum()), net_m3 = float(net), valid_pixels = valid, nodata_pixels = nodata)

    

    