# %%
import os
import sys
import numpy as np
import einops
from typing import Union, Optional, Tuple
import torch as t
from torch import Tensor
import torch.nn as nn
import torch.nn.functional as F
from jaxtyping import Float
import functools
from pathlib import Path
from torchvision import datasets, transforms
from torch.utils.data import DataLoader, Subset
from tqdm.notebook import tqdm

# Make sure exercises are in the path
chapter = r"chapter0_fundamentals"
exercises_dir = Path(f"{os.getcwd().split(chapter)[0]}/{chapter}/exercises").resolve()
section_dir = exercises_dir / "part2_cnns"
if str(exercises_dir) not in sys.path: sys.path.append(str(exercises_dir))

from plotly_utils import imshow, line, bar
from part2_cnns.utils import display_array_as_img, display_soln_array_as_img
import part2_cnns.tests as tests

MAIN = __name__ == "__main__"


# %%
arr = np.load(section_dir / "numbers.npy")

# %%
if MAIN:
    display_array_as_img(arr[0])

# Exercise 2
# %%
if MAIN:
    arr2 = einops.rearrange(arr, "b c h w -> c h (b w)")
    display_array_as_img(arr2)

# Exercise 3
# %%
if MAIN:
    arr3 = einops.repeat(arr[0], "c h w -> c (n h) w", n=2)
    display_array_as_img(arr3)

# Exercise 4
# %%
if MAIN:
    arr4 = einops.repeat(arr[:2], "b c h w -> c (b h) (2 w)")
    display_array_as_img(arr4)

# Exercise 5
# %%
if MAIN:
    arr5 = einops.repeat(arr[0], "c h w -> c (h 2) w")
    display_array_as_img(arr5)

# Exercise 6
# %%
if MAIN:
    arr6 = einops.rearrange(arr[0], "c h w -> h (c w)")
    display_array_as_img(arr6)

# Exercise 7
# %%
if MAIN:
    arr7 = einops.rearrange(arr, "(bh bw) c h w -> c (bh h) (bw w)", bh=2, bw=3)
    display_array_as_img(arr7)

# Exercise 8
# %%
if MAIN:
    arr8 = einops.reduce(arr, "b c h w -> h (b w)", "max")
    display_array_as_img(arr8)

# Exercise 9
# %%
if MAIN:
    arr9 = einops.reduce(arr, "b c h w -> h (b w)", "min")
    display_array_as_img(arr9)

# Exercise 10
# %%
if MAIN:
    arr10 = einops.reduce(arr, "b c h w -> h w", "min")
    display_array_as_img(arr10)