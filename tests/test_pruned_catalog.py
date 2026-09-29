"""Source-pinned contracts, not GPU inference or measured memory tests."""
from dataclasses import FrozenInstanceError, replace
import hashlib

import pytest

from h3server import backends, downloads
from h3server.domain import GPUInfo, HardwareInfo
from h3server.profiles import evaluate_profile
from h3server.paths import AppPaths


EXPECTED = {
    "h3-gguf-q4-k-m": (31550953118, "d74e644906fe95b8c7bf4837ba9d3f9392925f7301675a0993d419b62a4824a3"),
    "h3-gguf-q5-k-m": (34055739038, "abe739ac98bc14d6e4e56987198765b02899da632ad254c820f559b6dc4d1171"),
    "h3-convrot-mixed": (35889785333, "541c7797d815a165a3b71fa6cfe03cafa1ba658cb03af1dc9f17cfe3985e7f99"),
    "h3-convrot-int8": (40957152230, "f07a54277ca85b59e5243a924fb5c54d79768f820fc4155c1b614b6f613e9dbf"),
    "h3-nvfp4": (32515409407, "6ab7f0c48141e7919b32f925ca3def22e06a6aebeb9e0b6f5a0be0fe8409976f"),
}


@pytest.mark.parametrize("name", EXPECTED)
def test_exact_selected_artifact_contract(name):
    selected = backends.get_artifact_set(name)
    assert selected.weight_bytes == EXPECTED[name][0]
    assert selected.artifacts[0].sha256 == EXPECTED[name][1]
    assert {a.role for a in selected.artifacts} == {"diffusion_model", "text_encoder", "video_vae", "audio_vae"}
    assert len(selected.artifacts) == 4
    assert selected.artifacts[0].revision == ("76061e7fe4866dbc68903bfdf852430f0f1b2487"
        if "gguf" in name else "908eccad7e68751190d04c171956f163bfeed741")
    assert [(a.size_bytes, a.sha256) for a in selected.artifacts[1:]] == [
        (14173709116, "a97a557136057a8bcf6f2459b0875aeee3e8274408bd6616b669b41caefdb48d"),
        (5207808557, "88539d729fd7e9f9de765498428cd8e9152b8420317ec0e7542a362fffb7e0e1"),
        (605254869, "83043bf3afccf4cab4d7e114f6e04607e9687bef16930a2f6088d721a893d3c4"),
    ]
    assert all(a.revision == "908eccad7e68751190d04c171956f163bfeed741" for a in selected.artifacts[2:])
    assert all(a.revision == "a7169322485d0049380fb207fa17e9fb3ec40486" for a in selected.tokenizers)
    assert sum(a.size_bytes for a in selected.artifacts[1:]) == 19986772542
    assert selected.tokenizer_bytes == 4456704
    assert selected.selected_bytes == selected.weight_bytes + 4456704
    assert selected.runtime_bytes is None  # dependency/source provisioning, not an invented estimate
    assert selected.peak_weight_disk_bytes == selected.weight_bytes  # per-file rename, no cache copy
    assert len(selected.digest) == 64
    assert all("MiniMaxAI/MiniMax-H3" not in a.url for a in selected.artifacts)
    for a in selected.artifacts:
        assert a.url == f"https://huggingface.co/{a.repository}/resolve/{a.revision}/{a.path}"
        assert len(a.revision) == 40 and len(a.sha256) == 64
    with pytest.raises(FrozenInstanceError):
        selected.artifacts[0].size_bytes = 1
    assert backends.get_profile(name).min_ram_bytes is None


def test_defaults_remain_legacy_and_explicit_catalog_is_ranked():
    legacy = backends.load_profiles()
    assert len(legacy) == 2
    assert all(p.min_ram_bytes >= 96 * 1024**3 for p in legacy)
    assert backends.load_manifest()["model"]["repository"] == "MiniMaxAI/MiniMax-H3"
    expanded = backends.load_profiles(include_pruned=True)
    assert expanded[-2:] == legacy
    assert [p.name for p in expanded[:2]] == ["h3-gguf-q4-k-m", "h3-gguf-q5-k-m"]
    assert backends.get_profile(legacy[0].name) == legacy[0]
    with pytest.raises(ValueError, match="unavailable"):
        backends.get_artifact_set("h3-convrot-int4")
    with pytest.raises(ValueError, match="unknown"):
        backends.get_artifact_set("../../legacy")


def test_4090_29gib_is_not_rejected_by_legacy_ram_and_nvfp4_is_blocked():
    hw = HardwareInfo(gpus=(GPUInfo("NVIDIA GeForce RTX 4090", 24 * 1024**3),),
                      gpu_inventory_complete=True, ram_bytes=29 * 1024**3)
    for name in ("h3-gguf-q4-k-m", "h3-gguf-q5-k-m", "h3-convrot-mixed", "h3-convrot-int8"):
        result = evaluate_profile(hw, backends.get_profile(name))
        assert result.status != "unsupported"
        assert any("RAM" in r for r in result.reasons)
        assert backends.get_artifact_set(name).memory_status == "unmeasured"
    assert evaluate_profile(hw, backends.get_profile("h3-nvfp4")).status == "unsupported"
    from h3server.profiles import gpu_family_eligible
    assert gpu_family_eligible(hw, "blackwell") is False
    assert gpu_family_eligible(HardwareInfo(), "blackwell") is None
    assert gpu_family_eligible(replace(hw, gpus=(GPUInfo("mystery GPU", 192 * 1024**3),)), "blackwell") is None
    assert gpu_family_eligible(replace(hw, gpus=(GPUInfo("NVIDIA GeForce RTX 5090"),)), "blackwell") is True


def test_artifact_digest_binds_auxiliary_provenance_and_runtime():
    selected = backends.get_artifact_set("h3-gguf-q4-k-m")
    assert selected.artifacts[1].repository == "Winnougan/MiniMax-H3-INT4_Convrot_ComfyUI"
    assert selected.artifacts[1].revision == "6387f8cd370fd8b4deaa9aa7e9e1be4d7298e7df"
    for changes in ({"sha256": "a" * 64}, {"revision": "a" * 40}):
        modified = replace(selected, artifacts=(selected.artifacts[0], replace(selected.artifacts[1], **changes), *selected.artifacts[2:]))
        assert modified.digest != selected.digest
    assert replace(selected, runtime_id="different").digest != selected.digest
    assert selected.validation_status == "source-inspected-not-inference-certified"


@pytest.fixture
def tiny_catalog(tmp_path, monkeypatch):
    from h3server.backends import catalog
    original = catalog.get_artifact_set("h3-gguf-q4-k-m")
    contents = {a.local_relative_path: f"fixture-{a.role}".encode() for a in original.artifacts}
    selected = replace(original, artifacts=tuple(replace(a, size_bytes=len(contents[a.local_relative_path]),
        sha256=hashlib.sha256(contents[a.local_relative_path]).hexdigest()) for a in original.artifacts))
    original_lookup = catalog.get_artifact_set
    def lookup(name):
        return selected if name == selected.profile_name else original_lookup(name)
    monkeypatch.setattr(catalog, "get_artifact_set", lookup)
    monkeypatch.setattr(backends, "get_artifact_set", lookup)
    paths = AppPaths(tmp_path)
    for p in paths.directories():
        p.mkdir(parents=True, exist_ok=True)
    return paths, selected, contents


def consent(paths, selected):
    downloads.record_consent(paths, profile_name=selected.profile_name, artifact_set_digest=selected.digest,
        license_sha256=backends.load_manifest()["sources"]["license"]["sha256"],
        license_acknowledged=True, eligibility_confirmed=True, download_approved=True)


def test_profile_download_isolation_integrity_and_offline_snapshot(tiny_catalog):
    from test_downloads_progress import Response
    paths, selected, contents = tiny_catalog
    consent(paths, selected)
    calls = []
    def transport(paths, name, offset, **kwargs):
        calls.append((name, kwargs["profile_name"]))
        return Response(contents[name])
    snapshot = downloads.download_model(paths, profile_name=selected.profile_name, transport=transport)
    assert calls == [(a.local_relative_path, selected.profile_name) for a in selected.artifacts]
    assert downloads.verify_snapshot(paths, profile_name=selected.profile_name) == snapshot
    assert snapshot != downloads.snapshot_path(paths)
    assert selected.digest in str(snapshot)
    downloads.download_model(paths, profile_name=selected.profile_name,
                             transport=lambda *a, **k: pytest.fail("offline completed download"))
    with pytest.raises(ValueError, match="consent"):
        downloads.require_consent(paths, profile_name="h3-gguf-q5-k-m")
    with pytest.raises(ValueError, match="consent"):
        downloads.require_consent(paths)
    artifact = snapshot / selected.artifacts[1].local_relative_path
    artifact.write_bytes(b"x" * artifact.stat().st_size)
    with pytest.raises(ValueError, match="integrity"):
        downloads.verify_snapshot(paths, profile_name=selected.profile_name)
    artifact.unlink()
    with pytest.raises(ValueError, match="inventory"):
        downloads.verify_snapshot(paths, profile_name=selected.profile_name)


def test_pruned_consent_requires_exact_set_digest(tiny_catalog):
    paths, selected, _ = tiny_catalog
    with pytest.raises(ValueError, match="artifact"):
        downloads.record_consent(paths, profile_name=selected.profile_name, artifact_set_digest="0" * 64,
            license_sha256=backends.load_manifest()["sources"]["license"]["sha256"],
            license_acknowledged=True, eligibility_confirmed=True, download_approved=True)
    with pytest.raises(ValueError, match="consent"):
        downloads.download_model(paths, profile_name=selected.profile_name,
                                 transport=lambda *a, **k: pytest.fail("no consent"))
