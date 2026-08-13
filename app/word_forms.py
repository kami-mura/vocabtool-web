"""把词头展开成自然形态集合，用于句子/词表匹配（移植自 Streamlit 项目，去掉 lemminflect 依赖）。"""

from __future__ import annotations

import re
from functools import lru_cache

from . import vocab


def _regular_surface_forms(word: str) -> set[str]:
    """Return conservative regular forms for a lowercase single word."""
    if len(word) < 2:
        # 单字母词（i/a）没有规则屈折形式；盲目加 s/es/ed/ing 会产生
        # is/ied 等假词，污染句子匹配。
        return {word}
    forms = {word, f"{word}s", f"{word}ed", f"{word}ing"}
    if word.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(f"{word}es")
    if word.endswith("e") and len(word) > 2:
        forms.update({f"{word}d", f"{word[:-1]}ing"})
    if word.endswith("y") and len(word) > 2 and word[-2] not in "aeiou":
        forms.update({f"{word[:-1]}ies", f"{word[:-1]}ied"})
    if (
        len(word) >= 3
        and word[-1] not in "aeiouwxy"
        and word[-2] in "aeiou"
        and word[-3] not in "aeiou"
    ):
        forms.update({f"{word}{word[-1]}ed", f"{word}{word[-1]}ing"})
    return forms


@lru_cache(maxsize=4096)
def target_surface_forms(target: str) -> tuple[str, ...]:
    """Return a single-word target's exact and naturally inflected forms."""
    cleaned = re.sub(r"\s+", " ", str(target or "")).strip()
    if not cleaned:
        return ()
    if cleaned != cleaned.casefold():
        # 含大写字母的目标（March、iPhone）：严格按用户书写匹配，不展开词形。
        return (cleaned,)
    normalized = cleaned.casefold()
    if not re.fullmatch(r"[a-z]+", normalized):
        return (normalized,)

    forms = {normalized}
    # 目标词既可能是原型，也可能是变形（deteriorated / took / running）。
    # 统一展开到词头所在的整个词形族，保证正文里任何自然形态都能命中。
    headwords = {normalized}
    for variant, headword in vocab._IRREGULAR.items():
        if variant == normalized:
            headwords.add(headword)
    normalized_headword = vocab.normalize_word(normalized)
    if normalized_headword:
        headwords.add(normalized_headword)
    for headword in headwords:
        forms.add(headword)
        for variant, head in vocab._IRREGULAR.items():
            if head == headword:
                forms.add(variant)
        forms.update(_regular_surface_forms(headword))
    return tuple(sorted(forms, key=lambda value: (-len(value), value)))


@lru_cache(maxsize=4096)
def target_surface_pattern(target: str) -> re.Pattern[str] | None:
    """Compile a whole-term matcher for a target or its natural word forms."""
    forms = target_surface_forms(target)
    if not forms:
        return None
    alternatives = [re.escape(form).replace(r"\ ", r"\s+") for form in forms]
    # 全小写目标忽略大小写（允许句首大写）；带大写目标严格区分大小写。
    flags = re.IGNORECASE if forms[0] == forms[0].casefold() else 0
    return re.compile(
        rf"(?<![A-Za-z0-9])(?:{'|'.join(alternatives)})(?![A-Za-z0-9])",
        flags=flags,
    )
