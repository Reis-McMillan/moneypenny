import base64
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.exceptions import HTTPException

import config

_public_key_cache: Ed25519PublicKey | None = None


def _jwk_to_public_key(jwk: dict) -> Ed25519PublicKey:
    x = jwk["x"]
    x += "=" * (4 - len(x) % 4)
    raw = base64.urlsafe_b64decode(x)
    return Ed25519PublicKey.from_public_bytes(raw)


async def get_public_key() -> Ed25519PublicKey:
    global _public_key_cache
    if _public_key_cache is not None:
        return _public_key_cache

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(config.JWKS_URL)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail="Could not fetch authentication keys",
        ) from e

    jwks = response.json()
    jwk = next(
        (k for k in jwks["keys"] if k.get("alg") == "EdDSA"),
        None,
    )
    if jwk is None:
        raise HTTPException(
            status_code=503,
            detail="No EdDSA key found in JWKS",
        )

    _public_key_cache = _jwk_to_public_key(jwk)
    return _public_key_cache
