import pytest

from openlaw.corpus.quality import clean_judge_names, is_plausible_judge_name


@pytest.mark.parametrize(
    "name",
    ["Y.V. CHANDRACHUD", "SH. ANIL KUMAR SISODIA", "T.S. THAKUR", "DINESH MAHESHWARI"],
)
def test_accepts_real_judge_names(name: str) -> None:
    assert is_plausible_judge_name(name) is True


@pytest.mark.parametrize("name", ["which", "vide", "and", "the", "", "   ", None, "A"])
def test_rejects_observed_parser_noise(name: str | None) -> None:
    """'which' and 'vide' are real values found in vaquill Delhi HC judges lists."""
    assert is_plausible_judge_name(name) is False


def test_clean_judge_names_drops_noise_and_keeps_names() -> None:
    assert clean_judge_names(["SH. ANIL KUMAR SISODIA", "vide"]) == (
        "SH. ANIL KUMAR SISODIA",
    )


def test_clean_judge_names_on_all_noise_returns_empty() -> None:
    assert clean_judge_names(["which"]) == ()


@pytest.mark.parametrize("names", [None, []])
def test_clean_judge_names_handles_absent_input(names: list[str] | None) -> None:
    assert clean_judge_names(names) == ()
