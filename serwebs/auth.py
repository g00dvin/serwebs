from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.request import urlopen

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from jose.utils import base64url_decode

from serwebs.config import get_config

logger = logging.getLogger("serwebs.auth")
_bearer_scheme = HTTPBearer(auto_error=False)

# --- OIDC JWKS cache ---
_jwks_cache: dict = {}
_jwks_cache_time: float = 0.0
_JWKS_CACHE_TTL = 3600  # 1 hour


def _fetch_jwks(jwks_uri: str) -> dict:
    """Fetch and cache JWKS keys from the OIDC provider."""
    global _jwks_cache, _jwks_cache_time
    now = time.monotonic()
    if _jwks_cache and (now - _jwks_cache_time) < _JWKS_CACHE_TTL:
        return _jwks_cache
    try:
        logger.info("Fetching JWKS from %s", jwks_uri)
        resp = urlopen(jwks_uri, timeout=10)
        _jwks_cache = json.loads(resp.read())
        _jwks_cache_time = now
        logger.info("JWKS loaded: %d keys", len(_jwks_cache.get("keys", [])))
    except Exception as e:
        logger.error("Failed to fetch JWKS from %s: %s", jwks_uri, e)
        if not _jwks_cache:
            raise
    return _jwks_cache


def _get_oidc_jwks_uri() -> str:
    """Derive JWKS URI from OIDC config."""
    oidc = get_config().auth.oidc
    if oidc.jwks_uri:
        return oidc.jwks_uri
    # Auto-discover from issuer
    issuer = oidc.issuer.rstrip("/")
    discovery_url = issuer + "/.well-known/openid-configuration"
    try:
        resp = urlopen(discovery_url, timeout=10)
        config = json.loads(resp.read())
        return config["jwks_uri"]
    except Exception:
        # Fallback: common path
        return issuer + "/jwks/"


def _validate_oidc_token(token: str) -> Optional[dict]:
    """Validate a JWT token against the configured OIDC provider."""
    oidc = get_config().auth.oidc
    if not oidc.enabled:
        return None

    try:
        # Decode header to get key ID
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        # Fetch JWKS
        jwks_uri = _get_oidc_jwks_uri()
        jwks = _fetch_jwks(jwks_uri)

        # Find matching key
        rsa_key = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                rsa_key = key
                break

        if not rsa_key:
            logger.debug("OIDC: no matching key for kid=%s", kid)
            return None

        # Verify and decode
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
            audience=oidc.client_id,
            issuer=oidc.issuer.rstrip("/"),
            options={"verify_at_hash": False},
        )

        # Extract username
        username = payload.get(oidc.username_claim, payload.get("sub", "unknown"))

        # Extract role from groups/claims
        role = oidc.default_role
        groups = payload.get(oidc.role_claim, [])
        if isinstance(groups, str):
            groups = [groups]
        for g in oidc.admin_groups:
            if g in groups:
                role = "admin"
                break
        if role != "admin":
            for g in getattr(oidc, "viewer_groups", []):
                if g in groups:
                    role = "viewer"
                    break

        logger.debug("OIDC auth OK: user=%s, role=%s, groups=%s", username, role, groups)
        return {"username": username, "role": role}

    except JWTError as e:
        logger.debug("OIDC token validation failed: %s", e)
        return None
    except Exception as e:
        logger.error("OIDC unexpected error: %s", e)
        return None


# --- Local auth ---

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(username: str, role: str) -> str:
    cfg = get_config().auth
    expire = datetime.now(timezone.utc) + timedelta(minutes=cfg.token_expire_minutes)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, cfg.secret_key, algorithm=cfg.algorithm)


def decode_token(token: str) -> dict:
    cfg = get_config().auth
    return jwt.decode(token, cfg.secret_key, algorithms=[cfg.algorithm])


def authenticate_user(username: str, password: str) -> Optional[dict]:
    cfg = get_config().auth
    for u in cfg.users:
        if u.username == username and verify_password(password, u.password_hash):
            return {"username": u.username, "role": u.role}
    return None


def try_decode_any_token(token: str) -> Optional[dict]:
    """Try local JWT first, then OIDC if configured."""
    # Local JWT
    try:
        payload = decode_token(token)
        return {"username": payload["sub"], "role": payload["role"]}
    except JWTError:
        pass

    # OIDC
    result = _validate_oidc_token(token)
    if result:
        return result

    return None


def _extract_basic_auth(request: Request) -> Optional[dict]:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        username, password = decoded.split(":", 1)
        return authenticate_user(username, password)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> dict:
    # Try Bearer token (local JWT or OIDC)
    if credentials and credentials.credentials:
        user = try_decode_any_token(credentials.credentials)
        if user:
            return user

    # Fallback to Basic Auth
    user = _extract_basic_auth(request)
    if user:
        return user

    # Check query param (for WebSocket)
    token = request.query_params.get("token")
    if token:
        user = try_decode_any_token(token)
        if user:
            return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication",
        headers={"WWW-Authenticate": "Bearer"},
    )


_ROLE_HIERARCHY = {"admin": 3, "user": 2, "viewer": 1}


def require_role(role: str):
    """Require minimum role level. admin > user > viewer."""
    min_level = _ROLE_HIERARCHY.get(role, 0)

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        user_level = _ROLE_HIERARCHY.get(user["role"], 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' or higher required",
            )
        return user
    return _check
