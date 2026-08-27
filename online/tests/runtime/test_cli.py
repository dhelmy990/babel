from __future__ import annotations

from pathlib import Path

from babel_online.model.artifact import LoadedRealArtifact
from babel_online.runtime import cli


def test_real_launch_factory_registers_v2_and_injects_one_encoder(
    monkeypatch, tmp_path: Path, real_model_manifest, accepted_qwen_factory
) -> None:
    artifact_manifest = tmp_path / "artifact_manifest.json"
    artifact_manifest.write_text("verified payload")

    class Artifact:
        def path_for(self, name):
            assert name == "artifact_manifest.json"
            return artifact_manifest

    artifact = Artifact()
    model = real_model_manifest
    encoder = accepted_qwen_factory()
    load_calls = []
    encoder_calls = []
    registered = []
    monkeypatch.setattr(
        cli.DistilledArtifactV1,
        "load",
        lambda **values: load_calls.append(values) or artifact,
    )
    monkeypatch.setattr(cli, "build_real_original_manifest", lambda *_args, **_values: model)
    monkeypatch.setattr(
        cli.Qwen100Encoder,
        "from_artifact",
        lambda loaded, **values: encoder_calls.append((loaded, values)) or encoder,
    )
    database = type(
        "Database",
        (),
        {
            "bootstrap_real_model": lambda self, manifest, **values: registered.append(
                (manifest, values)
            )
        },
    )()

    launch, loaded_encoder = cli._load_real_launch(
        database,
        token="private-token",
        artifact_cache_dir=tmp_path / "artifact-cache",
        model_cache_dir=tmp_path / "model-cache",
        device="cpu",
    )

    assert isinstance(launch, LoadedRealArtifact)
    assert launch.manifest is model
    assert loaded_encoder is encoder
    assert len(load_calls) == len(encoder_calls) == len(registered) == 1
    assert load_calls[0]["revision"] == model.encoderRevision
    assert encoder_calls[0][1]["device"] == "cpu"
    assert registered[0][0] is model
