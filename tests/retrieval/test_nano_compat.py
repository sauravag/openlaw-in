from pathlib import Path

from openlaw.retrieval._nano_compat import (
    MODELING_FILENAME,
    _VENDORED_MODELING_SOURCE,
    prepare_local_model_dir,
)


def test_vendored_modeling_source_fixes_the_known_transformers_5_breakage() -> None:
    """voyage-4-nano's shipped code targets transformers 4.51 and breaks on 5.x:
    it calls create_causal_mask with the old `input_embeds`/`cache_position`
    kwargs. Our vendored copy must use the current signature instead."""
    assert "input_embeds=inputs_embeds" not in _VENDORED_MODELING_SOURCE
    assert "inputs_embeds=inputs_embeds" in _VENDORED_MODELING_SOURCE
    assert "cache_position=dummy_cache_position" not in _VENDORED_MODELING_SOURCE


def test_vendored_modeling_source_declares_config_class() -> None:
    """transformers 5.x's trust_remote_code class resolution needs config_class
    set on the custom model class; upstream's copy omits it."""
    assert "config_class = Qwen3Config" in _VENDORED_MODELING_SOURCE


def test_prepare_local_model_dir_preserves_other_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_text('{"hidden_size": 1024}')
    (source / MODELING_FILENAME).write_text("# broken upstream code\n")

    working = prepare_local_model_dir(source, tmp_path / "working")

    assert (working / "config.json").read_text() == '{"hidden_size": 1024}'


def test_prepare_local_model_dir_overwrites_the_modelling_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / MODELING_FILENAME).write_text("# broken upstream code\n")

    working = prepare_local_model_dir(source, tmp_path / "working")

    installed = (working / MODELING_FILENAME).read_text()
    assert installed == _VENDORED_MODELING_SOURCE


def test_prepare_local_model_dir_preserves_nested_subdirectories(tmp_path: Path) -> None:
    """Real model dirs nest files (e.g. `1_Pooling/config.json`) -- those
    subdirectories must be recreated, not just their top-level siblings."""
    source = tmp_path / "source"
    (source / "1_Pooling").mkdir(parents=True)
    (source / "1_Pooling" / "config.json").write_text('{"pooling_mode": "mean"}')

    working = prepare_local_model_dir(source, tmp_path / "working")

    nested = working / "1_Pooling" / "config.json"
    assert nested.read_text() == '{"pooling_mode": "mean"}'


def test_prepare_local_model_dir_returns_the_working_dir(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    result = prepare_local_model_dir(source, tmp_path / "working")

    assert result == tmp_path / "working"


def test_prepare_local_model_dir_resolves_relative_blob_symlinks(tmp_path: Path) -> None:
    """Regression: the Hugging Face Hub cache stores files as symlinks like
    `snapshots/<rev>/config.json -> ../../blobs/<hash>`. Copying that relative
    link verbatim into a working dir at a different path depth (as
    `shutil.copytree(symlinks=True)` did) produces a broken symlink. Every linked
    file in the working dir must resolve to real content regardless of depth."""
    hf_cache = tmp_path / "hf_cache"
    blobs = hf_cache / "blobs"
    blobs.mkdir(parents=True)
    blob = blobs / "deadbeef"
    blob.write_text("real config content")

    snapshot = hf_cache / "snapshots" / "somerevision"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").symlink_to(Path("../../blobs/deadbeef"))
    assert (snapshot / "config.json").read_text() == "real config content"

    working = prepare_local_model_dir(snapshot, tmp_path / "working")

    assert (working / "config.json").read_text() == "real config content"


def test_prepare_local_model_dir_is_rebuilt_fresh_each_call(tmp_path: Path) -> None:
    """A stale leftover file in a previously-prepared working dir must not survive
    a second prepare call against a different source."""
    source = tmp_path / "source"
    source.mkdir()

    working_dir = tmp_path / "working"
    working_dir.mkdir()
    (working_dir / "stale.txt").write_text("leftover from a previous run")

    prepare_local_model_dir(source, working_dir)

    assert not (working_dir / "stale.txt").exists()
