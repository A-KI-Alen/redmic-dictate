from __future__ import annotations


_CHAR_REPLACEMENTS = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
}


def strip_prompt_leak(text: str, prompt: str) -> str:
    cleaned = str(text or "").strip()
    prompt = str(prompt or "").strip()
    if not cleaned or not prompt:
        return cleaned

    normalized_prompt = _normalize(prompt)
    if not normalized_prompt:
        return cleaned

    while True:
        normalized_text, end_positions = _normalize_with_end_positions(cleaned)
        match_at = normalized_text.find(normalized_prompt)
        if match_at < 0:
            return cleaned.strip()
        if len(end_positions) < match_at + len(normalized_prompt):
            return ""
        start_at = 0 if match_at == 0 else end_positions[match_at - 1]
        end_at = end_positions[match_at + len(normalized_prompt) - 1]
        left = cleaned[:start_at].rstrip(" \t\r\n:.-")
        right = cleaned[end_at:].lstrip(" \t\r\n:.-")
        if left and right:
            next_cleaned = f"{left} {right}"
        else:
            next_cleaned = left or right
        if next_cleaned == cleaned:
            return cleaned.strip()
        cleaned = next_cleaned


def _normalize(text: str) -> str:
    normalized, _ = _normalize_with_end_positions(text)
    return normalized


def _normalize_with_end_positions(text: str) -> tuple[str, list[int]]:
    output: list[str] = []
    end_positions: list[int] = []
    last_was_space = False

    for index, char in enumerate(text):
        if char.isspace():
            if output and not last_was_space:
                output.append(" ")
                end_positions.append(index + 1)
                last_was_space = True
            continue

        lowered = char.casefold()
        replacement = _CHAR_REPLACEMENTS.get(lowered, lowered)
        for replacement_char in replacement:
            output.append(replacement_char)
            end_positions.append(index + 1)
        last_was_space = False

    while output and output[-1] == " ":
        output.pop()
        end_positions.pop()
    return "".join(output), end_positions
