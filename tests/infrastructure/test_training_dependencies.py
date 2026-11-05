"""Release provenance and source selection; no external checkout or GPU required."""

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from behave.train.slime_launch import megatron_source

ROOT = Path(__file__).resolve().parents[2]


def test_training_source_pins_and_patch_integrity():
    manifest = tomllib.loads((ROOT / "external/manifest.toml").read_text())
    for name in ("slime", "sglang", "megatron-lm", "apex"):
        source = manifest[name]
        assert re.fullmatch(r"[a-f0-9]{40}", source["commit"])
        assert source["upstream"].startswith("https://github.com/")
        assert set(source.get("patch_sha256", {})) == set(source["patches"])
        for patch in source["patches"]:
            content = (ROOT / "external/patches" / name / patch).read_bytes()
            assert hashlib.sha256(content).hexdigest() == source["patch_sha256"][patch]


def test_training_sglang_patch_includes_previously_untracked_sources():
    content = (ROOT / "external/patches/sglang/0001-training-runtime.patch").read_text()
    additions = (
        "python/sglang/jit_kernel/glm5_router_gemm.py",
        "python/sglang/jit_kernel/tests/test_glm5_router_gemm.py",
        "python/sglang/srt/weight_sync/local_checkpoint.py",
        "test/registered/moe/test_deepep_ll_prefill_staging.py",
    )
    for path in additions:
        assert f"diff --git a/{path} b/{path}\nnew file mode" in content


def test_native_versions_agree_with_the_experiment_runtime_identity():
    observed = json.loads((ROOT / "infra/train/native_runtime.json").read_text())
    manifest = tomllib.loads((ROOT / "external/manifest.toml").read_text())
    config = json.loads((ROOT / "configs/train/qwen27b_rl.example.json").read_text())["settings"]
    revision = config["runtime_revision"]
    assert manifest["slime"]["commit"][:12] in revision
    assert f"sglang-{observed['packages']['sglang']}" in revision
    assert observed["packages"]["torch"] == "2.11.0+cu129"


def test_megatron_explicit_path_does_not_depend_on_slime_location():
    config = {"slime_source": "/other/location/slime", "megatron_source": "/sources/megatron"}
    assert megatron_source(config) == Path("/sources/megatron")


@pytest.mark.parametrize("value", [None, "", "relative/source", "/", "/sources/../wrong", "\n/source"])
def test_invalid_explicit_megatron_source_does_not_silently_fall_back(value):
    with pytest.raises(ValueError, match="megatron_source"):
        megatron_source({"slime_source": "/sources/slime", "megatron_source": value})
