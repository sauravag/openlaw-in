"""The localised note on an account-deletion response.

States what WAS and was NOT deleted -- erasure that hides its boundary is
worse than erasure that states it. Hindi is best-effort pending native-speaker
review, as elsewhere. Never claims the sign-in identity was removed unless it
was.
"""

from __future__ import annotations

_RETAINED_EN = (
    "Your conversations, uploaded documents and excerpts have been erased, "
    "including any linked to this browser without an account. "
    "A record of who accessed each document (the access log) is kept, without "
    "any document content."
)
_RETAINED_HI = (
    "आपकी बातचीत, अपलोड किए गए दस्तावेज़ और उनके अंश मिटा दिए गए हैं, उनके साथ भी जो बिना "
    "खाते के इस ब्राउज़र से जुड़े थे। हर दस्तावेज़ को किसने "
    "देखा, इसका रिकॉर्ड (एक्सेस लॉग) बिना किसी दस्तावेज़ सामग्री के रखा गया है।"
)
#: What `account_deleted` (refusal copy) repeats after its opening sentence,
#: shared so the erased/kept facts are stated in exactly one place.
RETAINED_NOTES: tuple[str, str] = (_RETAINED_EN, _RETAINED_HI)
_IDENTITY_REMOVED_EN = " Your sign-in identity has also been removed."
_IDENTITY_REMOVED_HI = " आपकी साइन इन पहचान भी हटा दी गई है।"
_IDENTITY_REMAINS_EN = (
    " Your sign-in identity (your email address at our sign-in provider) could "
    "not be removed and may remain. To have it removed, contact the site "
    "operator and ask for your sign-in identity to be deleted."
)
_IDENTITY_REMAINS_HI = (
    " आपकी साइन इन पहचान (साइन इन प्रदाता के पास आपका ईमेल पता) हटाई नहीं जा सकी और "
    "बनी रह सकती है। इसे हटवाने के लिए साइट संचालक से संपर्क करें और अपनी साइन इन "
    "पहचान मिटाने को कहें।"
)


def deletion_notes(identity_removed: bool) -> tuple[str, str]:
    """Return the `(english, hindi)` note for the deletion outcome."""
    if identity_removed:
        return _RETAINED_EN + _IDENTITY_REMOVED_EN, _RETAINED_HI + _IDENTITY_REMOVED_HI
    return _RETAINED_EN + _IDENTITY_REMAINS_EN, _RETAINED_HI + _IDENTITY_REMAINS_HI
