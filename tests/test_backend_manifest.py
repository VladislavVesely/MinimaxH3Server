"""Regression checks for the source-inspected, not execution-certified H3 backend."""

import hashlib
from importlib import resources
import json
from pathlib import PurePosixPath, Path
import re
import tomllib
from urllib.parse import urlsplit

from h3server.backends import load_manifest, load_profiles
from h3server.domain import Capability, CapabilityStatus, HardwareInfo
from h3server.profiles import RecommendationStatus, evaluate_profile


DIFFUSERS_REVISION = "5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2"
MODEL_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"
MINIMAX_REVISION = "d21241f0a4b3acbb34c97dae47fa417b7065e438"


def test_manifest_pins_exact_upstream_revisions():
    manifest = load_manifest()
    assert manifest["schema_version"] == 1
    assert manifest["backend"]["revision"] == DIFFUSERS_REVISION
    assert manifest["model"]["revision"] == MODEL_REVISION
    assert manifest["upstream"]["revision"] == MINIMAX_REVISION
    for section in ("backend", "model", "upstream"):
        assert re.fullmatch(r"[0-9a-f]{40}", manifest[section]["revision"])


def test_manifest_is_local_only_and_not_an_execution_claim():
    manifest = load_manifest()
    assert manifest["backend"]["workflow"] == "t2va"
    assert manifest["backend"]["execution"] == "local"
    assert manifest["backend"]["cloud_fallback"] is False
    assert manifest["backend"]["generation_endpoints"] == []
    assert manifest["backend"]["execution_tested"] is False
    assert manifest["backend"]["adapter_implemented"] is False


def test_complete_selected_artifacts_are_preserved():
    manifest = load_manifest()
    files = manifest["model"]["files"]
    assert len(files) == 62
    assert sum(item["size"] for item in files) == manifest["model"]["total_bytes"] == 144051160615
    # Independent fingerprint of the supplied selected-files.json inventory. Covers
    # every name, size, Git blob ID, LFS SHA256, LFS size and pointer size.
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == "f53d55b79c89b8566c6ae498d7a37662d8199ff78e45926b34f281b19360bc78"


def test_artifacts_are_only_confined_t2va_components():
    manifest = load_manifest()
    files = manifest["model"]["files"]
    names = [item["rfilename"] for item in files]
    assert len(names) == len(set(names))
    roots = {"LICENSE", "model_index.json", "modular_model_index.json", "audio_scheduler", "audio_vae",
             "processor", "scheduler", "text_encoder", "tokenizer", "transformer", "vae"}
    assert {PurePosixPath(name).parts[0] for name in names} == roots
    for item in files:
        name = item["rfilename"]
        assert not PurePosixPath(name).is_absolute()
        assert ".." not in PurePosixPath(name).parts and "\\" not in name
        assert not any(excluded in name.lower() for excluded in ("fl2va", "ref2va", "transformer_ref"))
        assert type(item["size"]) is int and item["size"] > 0
        assert re.fullmatch(r"[0-9a-f]{40}", item["blobId"])
        if name.endswith(".safetensors"):
            assert re.fullmatch(r"[0-9a-f]{64}", item["lfs"]["sha256"])
            assert item["lfs"]["size"] == item["size"]
        url = manifest["model"]["download_url_template"].format(path=name)
        assert url == f"https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/{MODEL_REVISION}/{name}"


def test_sources_are_durable_pinned_links_with_content_hashes():
    manifest = load_manifest()
    sources = manifest["sources"]
    assert {"h3_docs", "h3_pipeline", "h3_denoise", "h3_transformer", "lora_loader", "modular_pipeline",
            "model_index", "model_info", "license", "diffusers_setup", "export"} <= sources.keys()
    for source in sources.values():
        url = urlsplit(source["url"])
        assert url.scheme == "https"
        assert url.hostname in {"raw.githubusercontent.com", "huggingface.co", "pypi.org"}
        if url.hostname != "pypi.org":
            assert any(revision in url.path for revision in (DIFFUSERS_REVISION, MODEL_REVISION, MINIMAX_REVISION))
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        assert source["observation"]


def test_capabilities_distinguish_source_support_from_execution():
    manifest = load_manifest()
    capabilities = manifest["capabilities"]
    for feature, status in (("lora", CapabilityStatus.SUPPORTED), ("sparse_attention", CapabilityStatus.UNSUPPORTED)):
        item = capabilities[feature]
        capability = Capability(item["status"], tuple(item["evidence"]))
        assert capability.status is status
        assert item["basis"] == "source_inspection"
        assert item["execution_tested"] is False
        assert all(key in manifest["sources"] for key in item["sources"])
        assert item["sources"]
    assert "MiniMaxH3LoraLoaderMixin" in " ".join(capabilities["lora"]["evidence"])
    assert "full self-attention" in " ".join(capabilities["sparse_attention"]["evidence"])
    assert "Sage" in " ".join(capabilities["sparse_attention"]["evidence"])


def test_license_requires_explicit_acknowledgement_not_hub_gating():
    manifest = load_manifest()
    license_info = manifest["license"]
    assert license_info["name"] == "MiniMax H3 Community License Agreement"
    assert license_info["acknowledgement_required"] is True
    assert license_info["acknowledged"] is False
    assert license_info["hub_gated_at_inspection"] is False
    assert license_info["source"] == "license"
    assert license_info["excluded_territories"] == ["European Union", "United Kingdom", "Republic of Korea", "United States of America"]
    assert license_info["commercial_authorization_threshold_usd_yearly"] == 20000000
    assert license_info["obligations"]


def test_generation_constraints_are_h3_specific():
    manifest = load_manifest()
    constraints = manifest["generation_constraints"]
    assert constraints["fps"] == 24
    assert constraints["frame_alignment"] == {"multiple": 17, "offset": 5, "rounding": "up"}
    assert constraints["duration_seconds_after_alignment"] == {"min": 5, "max": 15}
    assert constraints["spatial_multiple"] == 32
    assert constraints["model_evaluations"] == "num_inference_steps - 1"
    assert constraints["unsupported_inputs"] == ["negative_prompt", "guidance_scale", "sparse_attention"]


def test_candidate_dependencies_are_not_a_tested_lock():
    manifest = load_manifest()
    deps = manifest["candidate_environment"]
    assert deps["status"] == "unresolved_untested_constraints"
    assert deps["python"] == ">=3.11"
    assert deps["constraints"]["diffusers"] == f"git+https://github.com/huggingface/diffusers.git@{DIFFUSERS_REVISION}"
    assert deps["constraints"]["huggingface-hub"] == ">=1.32.0,<2.0"
    assert deps["constraints"]["torch"] == "==2.14.0"
    assert deps["constraints"]["torchvision"] == "==0.29.0"
    assert deps["metadata_requirements"]["torchvision"] == ["torch==2.14.0"]
    assert "huggingface-hub>=1.32.0,<2.0" in deps["metadata_requirements"]["diffusers"]
    assert deps["observed_pypi_versions"]["huggingface-hub"] == "2.0.0"
    assert deps["unverified"]


def test_profiles_reuse_existing_contract_without_claiming_certification():
    manifest = load_manifest()
    profiles = load_profiles()
    assert {profile.name for profile in profiles} == {"h3-bf16-cpu-offload-experimental", "h3-int8-group-offload-experimental"}
    for profile in profiles:
        assert profile.status is RecommendationStatus.EXPERIMENTAL
        assert profile.backend_revision == DIFFUSERS_REVISION
        assert profile.verification_evidence == ()
        assert profile.distributions == profile.driver_versions == profile.gpu_names == ()
        assert profile.disk.required_bytes > manifest["model"]["total_bytes"]
        assert "local conservative estimate" in " ".join(profile.evidence + profile.disk.evidence)
        assert evaluate_profile(HardwareInfo(), profile).status is RecommendationStatus.UNKNOWN
    int8 = next(profile for profile in profiles if "int8" in profile.name)
    assert int8.min_ram_bytes == 96 * 1024**3


def test_loader_returns_fresh_data_and_works_outside_repository(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    first = load_manifest()
    first["model"]["files"].clear()
    assert len(load_manifest()["model"]["files"]) == 62
    assert resources.files("h3server.backends").joinpath("manifest.json").is_file()


def test_manifest_is_explicit_package_data():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert "manifest.json" in project["tool"]["setuptools"].get("package-data", {}).get("h3server.backends", [])
