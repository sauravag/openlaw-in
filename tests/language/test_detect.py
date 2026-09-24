import pytest

from openlaw.language.detect import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    detect_by_script,
    detect_language,
)


def test_default_language_is_english() -> None:
    assert DEFAULT_LANGUAGE == "en"


def test_supported_languages_cover_the_largest_indian_languages() -> None:
    for code in ("en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "or"):
        assert code in SUPPORTED_LANGUAGES


def test_detects_devanagari_as_hindi() -> None:
    assert detect_by_script("मेरा मकान मालिक जमा राशि वापस नहीं कर रहा") == "hi"


def test_detects_tamil_script() -> None:
    assert detect_by_script("என் நில உரிமையாளர் வைப்புத்தொகையை") == "ta"


def test_detects_bengali_script() -> None:
    assert detect_by_script("আমার বাড়িওয়ালা জমা ফেরত দিচ্ছে না") == "bn"


def test_detects_telugu_script() -> None:
    assert detect_by_script("నా ఇంటి యజమాని డిపాజిట్ తిరిగి ఇవ్వడం లేదు") == "te"


def test_detects_gujarati_script() -> None:
    assert detect_by_script("મારો મકાનમાલિક થાપણ પરત આપતો નથી") == "gu"


def test_script_detection_returns_none_for_latin_text() -> None:
    """Latin script is ambiguous — it needs statistical detection, not ranges."""
    assert detect_by_script("my landlord kept my deposit") is None


def test_script_detection_ignores_a_stray_character() -> None:
    """One Devanagari character in an English sentence is not Hindi."""
    assert detect_by_script("my landlord क kept my deposit") is None


def test_detects_english_end_to_end() -> None:
    assert detect_language("my landlord will not return my security deposit") == "en"


def test_detects_hindi_end_to_end() -> None:
    assert detect_language("मेरा मकान मालिक जमा राशि वापस नहीं कर रहा है") == "hi"


@pytest.mark.parametrize("text", ["", "   ", "123 456"])
def test_falls_back_to_english_for_unusable_input(text: str) -> None:
    assert detect_language(text) == DEFAULT_LANGUAGE


def test_falls_back_to_english_for_an_unsupported_language() -> None:
    """A language we cannot serve must not be claimed as supported."""
    assert detect_language("Ceci est une question juridique française") == (
        DEFAULT_LANGUAGE
    )
