def find_token(
    external_tokens: list[dict],
    provider_id: str,
    subject: str | None = None
) -> dict | None:
    for token in external_tokens:
        if token.get('provider_id') != provider_id:
            continue
        if subject is None or token.get('subject') == subject:
            return token
    return None
