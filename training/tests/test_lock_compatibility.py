from __future__ import annotations

import importlib.metadata
import os
import re
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUN_LOCK_SMOKE = os.environ.get("BABEL_RUN_LOCK_COMPATIBILITY_SMOKE") == "1"
RUN_GPU_SMOKE = os.environ.get("BABEL_RUN_GPU_SMOKE") == "1"
PYPI_INDEX = "https://pypi.org/simple"
PYTORCH_CU128_INDEX = "https://download.pytorch.org/whl/cu128"


def test_training_runtime_pins_archived_torchdata_0_11() -> None:
    pyproject = (REPOSITORY_ROOT / "training" / "pyproject.toml").read_text()

    assert '"torchdata==0.11.0"' in pyproject


def test_training_lock_pins_official_torch_cu128_without_cuda_13() -> None:
    pyproject = (REPOSITORY_ROOT / "training" / "pyproject.toml").read_text()
    lock = (REPOSITORY_ROOT / "training" / "requirements-colab.lock").read_text()

    assert '"torch==2.11.0+cu128"' in pyproject
    assert f"--index-url={PYPI_INDEX}" in lock
    assert f"--extra-index-url {PYTORCH_CU128_INDEX}" in lock
    assert "torch==2.11.0+cu128" in lock
    assert "cu13" not in lock.lower()
    assert not re.search(r"^cuda-toolkit.*==13(?:\.|$)", lock, re.MULTILINE)
    assert not re.search(r"^nvidia-[^=\n]+==13(?:\.|$)", lock, re.MULTILINE)


@pytest.mark.skipif(
    not RUN_LOCK_SMOKE,
    reason="runs only in the clean training lock environment",
)
def test_archived_torchdata_0_11_works_with_current_torch_2_11_lock() -> None:
    # TorchData is archived and its official matrix ends at Torch 2.6. This
    # smoke test is the compatibility contract for the current security pin.
    import peft
    import torch
    import transformers
    from torchdata.stateful_dataloader import StatefulDataLoader

    assert importlib.metadata.version("torch") == "2.11.0+cu128"
    assert importlib.metadata.version("torchdata") == "0.11.0"
    assert importlib.metadata.version("transformers") == "4.51.3"
    assert importlib.metadata.version("peft") == "0.15.2"

    loader = StatefulDataLoader(list(range(6)), batch_size=2, shuffle=False)
    iterator = iter(loader)
    assert next(iterator).tolist() == [0, 1]
    state = loader.state_dict()

    restored = StatefulDataLoader(list(range(6)), batch_size=2, shuffle=False)
    restored.load_state_dict(state)

    assert [batch.tolist() for batch in restored] == [[2, 3], [4, 5]]
    assert torch.__version__ == "2.11.0+cu128"
    assert torch.version.cuda == "12.8"
    assert transformers.__version__ == "4.51.3"
    assert peft.__version__ == "0.15.2"


@pytest.mark.skipif(
    not RUN_GPU_SMOKE,
    reason="opt-in Colab GPU gate",
)
def test_torch_cu128_gpu_allocation_and_forward() -> None:
    import torch

    assert torch.__version__ == "2.11.0+cu128"
    assert torch.version.cuda == "12.8"
    assert torch.cuda.is_available()

    device = torch.device("cuda")
    inputs = torch.ones((2, 4), device=device)
    model = torch.nn.Linear(4, 2).to(device)
    outputs = model(inputs)

    assert outputs.shape == (2, 2)
    assert outputs.device.type == "cuda"
