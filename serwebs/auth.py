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


def _authenticate_ldap(username: str, password: str) -> Optional[dict]:
    """Authenticate against LDAP/Active Directory."""
    cfg = get_config().auth.ldap
    if not cfg.enabled:
        return None
    try:
        import ldap3
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl
    except ImportError:
        logger.warning("ldap3 not installed — LDAP auth disabled. Install with: pip install ldap3")
        return None

    try:
        tls = None
        if cfg.use_ssl or cfg.start_tls:
            tls_kwargs = {"validate": ssl.CERT_NONE}
            if cfg.ca_cert_file:
                tls_kwargs = {"validate": ssl.CERT_REQUIRED, "ca_certs_file": cfg.ca_cert_file}
            tls = Tls(**tls_kwargs)

        server = Server(cfg.url, use_ssl=cfg.use_ssl, tls=tls, get_info=ALL)

        # Bind with service account to search for user
        conn = Connection(server, user=cfg.bind_dn, password=cfg.bind_password, auto_bind=True)
        if cfg.start_tls:
            conn.start_tls()

        # Search for user
        search_filter = cfg.user_filter.replace("{username}", username)
        conn.search(cfg.user_base_dn, search_filter, search_scope=SUBTREE,
                    attributes=[cfg.username_attribute, "dn"])
        if not conn.entries:
            conn.unbind()
            return None

        user_dn = str(conn.entries[0].entry_dn)
        conn.unbind()

        # Bind as user to verify password
        user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()

        # Determine role from groups
        role = cfg.default_role
        if cfg.group_base_dn:
            group_conn = Connection(server, user=cfg.bind_dn, password=cfg.bind_password, auto_bind=True)
            group_filter = cfg.group_filter.replace("{user_dn}", user_dn)
            group_conn.search(cfg.group_base_dn, group_filter, search_scope=SUBTREE, attributes=["cn"])
            groups = [str(e.cn) for e in group_conn.entries]
            group_conn.unbind()

            for g in cfg.admin_groups:
                if g in groups:
                    role = "admin"
                    break
            if role != "admin":
                for g in cfg.viewer_groups:
                    if g in groups:
                        role = "viewer"
                        break

        logger.info("LDAP auth OK: user=%s, role=%s", username, role)
        return {"username": username, "role": role}

    except Exception as e:
        logger.debug("LDAP auth failed for %s: %s", username, e)
        return None


def _authenticate_radius(username: str, password: str) -> Optional[dict]:
    """Authenticate against RADIUS server."""
    cfg = get_config().auth.radius
    if not cfg.enabled:
        return None
    try:
        from pyrad.client import Client
        from pyrad.dictionary import Dictionary
        from pyrad import packet
    except ImportError:
        logger.warning("pyrad not installed — RADIUS auth disabled. Install with: pip install pyrad")
        return None

    try:
        # Use built-in minimal dictionary
        import tempfile, os
        dict_content = (
            "ATTRIBUTE\tUser-Name\t1\tstring\n"
            "ATTRIBUTE\tUser-Password\t2\tstring\n"
            "ATTRIBUTE\tFilter-Id\t11\tstring\n"
            "ATTRIBUTE\tReply-Message\t18\tstring\n"
            "ATTRIBUTE\tNAS-Identifier\t32\tstring\n"
        )
        dict_fd, dict_path = tempfile.mkstemp(suffix=".dict")
        with os.fdopen(dict_fd, "w") as df:
            df.write(dict_content)

        try:
            srv = Client(
                server=cfg.server,
                secret=cfg.secret.encode(),
                dict=Dictionary(dict_path),
            )
            srv.timeout = cfg.timeout
            srv.retries = cfg.retries

            req = srv.CreateAuthPacket(code=packet.AccessRequest, User_Name=username)
            req["User-Password"] = req.PwCrypt(password)
            if cfg.nas_identifier:
                req["NAS-Identifier"] = cfg.nas_identifier

            reply = srv.SendPacket(req)
        finally:
            os.unlink(dict_path)

        if reply.code == packet.AccessAccept:
            role = cfg.default_role
            filter_ids = reply.get("Filter-Id", [])
            for fid in filter_ids:
                fid_str = fid if isinstance(fid, str) else fid.decode()
                if fid_str == cfg.admin_filter_id:
                    role = "admin"
                    break
                if fid_str == cfg.viewer_filter_id:
                    role = "viewer"
            logger.info("RADIUS auth OK: user=%s, role=%s", username, role)
            return {"username": username, "role": role}
        else:
            logger.debug("RADIUS auth rejected for %s (code=%d)", username, reply.code)
            return None

    except Exception as e:
        logger.debug("RADIUS auth failed for %s: %s", username, e)
        return None


def _authenticate_tacacs(username: str, password: str) -> Optional[dict]:
    """Authenticate against TACACS+ server."""
    cfg = get_config().auth.tacacs
    if not cfg.enabled:
        return None
    try:
        from tacacs_plus.client import TACACSClient
        from tacacs_plus.flags import TAC_PLUS_AUTHEN_STATUS_PASS
    except ImportError:
        logger.warning("tacacs_plus not installed — TACACS+ auth disabled. Install with: pip install tacacs_plus")
        return None

    try:
        cli = TACACSClient(cfg.server, cfg.port, cfg.secret, timeout=cfg.timeout)
        authen = cli.authenticate(username, password)

        if authen.valid and authen.status == TAC_PLUS_AUTHEN_STATUS_PASS:
            # Try authorization to get privilege level
            role = cfg.default_role
            try:
                author = cli.authorize(username, arguments=[f"service={cfg.service}"])
                if author.valid:
                    for arg in author.arguments:
                        arg_str = arg if isinstance(arg, str) else arg.decode()
                        if arg_str.startswith("priv-lvl="):
                            priv = int(arg_str.split("=", 1)[1])
                            if priv >= cfg.admin_priv_lvl:
                                role = "admin"
                            elif priv <= cfg.viewer_priv_lvl:
                                role = "viewer"
                            break
            except Exception:
                pass

            logger.info("TACACS+ auth OK: user=%s, role=%s", username, role)
            return {"username": username, "role": role}
        else:
            logger.debug("TACACS+ auth rejected for %s", username)
            return None

    except Exception as e:
        logger.debug("TACACS+ auth failed for %s: %s", username, e)
        return None


def authenticate_user(username: str, password: str) -> Optional[dict]:
    cfg = get_config().auth

    # 1. Local users (config.toml)
    for u in cfg.users:
        if u.username == username and verify_password(password, u.password_hash):
            return {"username": u.username, "role": u.role}

    # 2. Runtime users (managed via API, stored in data/users.json)
    from serwebs.config import load_runtime_users
    for u in load_runtime_users():
        if u.get("username") == username and verify_password(password, u.get("password_hash", "")):
            return {"username": u["username"], "role": u.get("role", "user")}

    # 3. LDAP
    result = _authenticate_ldap(username, password)
    if result:
        return result

    # 4. RADIUS
    result = _authenticate_radius(username, password)
    if result:
        return result

    # 5. TACACS+
    result = _authenticate_tacacs(username, password)
    if result:
        return result

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
