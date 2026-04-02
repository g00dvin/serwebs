/**
 * Auth module — JWT token management + OIDC support.
 */
(function () {
  var TOKEN_KEY = "serwebs_token";
  var USER_KEY = "serwebs_user";

  /**
   * Generate a cryptographically secure random string (hex).
   * Falls back to Math.random only if crypto API is unavailable.
   */
  function _secureRandom(bytes) {
    bytes = bytes || 16;
    if (window.crypto && window.crypto.getRandomValues) {
      var buf = new Uint8Array(bytes);
      window.crypto.getRandomValues(buf);
      return Array.from(buf).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
    }
    // Fallback for very old browsers — not cryptographically secure
    var s = "";
    for (var i = 0; i < bytes * 2; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s;
  }

  /**
   * Safely decode a JWT payload. Returns null on any malformed input.
   */
  function _safeDecodePayload(token) {
    try {
      var parts = token.split(".");
      if (parts.length !== 3) return null;
      // Base64url -> base64
      var b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      var json = decodeURIComponent(
        atob(b64).split("").map(function (c) {
          return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
        }).join("")
      );
      var payload = JSON.parse(json);
      if (typeof payload !== "object" || payload === null) return null;
      return payload;
    } catch (e) {
      return null;
    }
  }

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function getUser() {
    try {
      var raw = localStorage.getItem(USER_KEY);
      if (!raw) return null;
      var user = JSON.parse(raw);
      if (typeof user !== "object" || !user.username || !user.role) return null;
      return user;
    } catch (e) {
      return null;
    }
  }

  function isLoggedIn() {
    var token = getToken();
    if (!token) return false;
    var payload = _safeDecodePayload(token);
    if (!payload || !payload.exp) return false;
    return payload.exp * 1000 > Date.now();
  }

  function _storeToken(accessToken) {
    var payload = _safeDecodePayload(accessToken);
    if (!payload || !payload.sub) {
      throw new Error("Invalid token");
    }
    localStorage.setItem(TOKEN_KEY, accessToken);
    localStorage.setItem(USER_KEY, JSON.stringify({ username: payload.sub, role: payload.role || "viewer" }));
    return payload;
  }

  async function login(username, password) {
    var resp = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password }),
    });
    if (!resp.ok) {
      var err = await resp.json().catch(function () { return {}; });
      throw new Error(err.detail || "Login failed");
    }
    var data = await resp.json();
    return _storeToken(data.access_token);
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  function authHeaders() {
    var token = getToken();
    return token ? { Authorization: "Bearer " + token } : {};
  }

  // --- OIDC ---

  async function fetchAuthConfig() {
    try {
      var resp = await fetch("/auth/config");
      if (resp.ok) return await resp.json();
    } catch (e) {}
    return { local_auth: true };
  }

  function startOIDCLogin(oidcConfig) {
    // Build OIDC authorization URL (Authorization Code flow with implicit id_token)
    var redirectUri = window.location.origin + "/oidc/callback";
    var state = _secureRandom(32);
    sessionStorage.setItem("oidc_state", state);

    var params = new URLSearchParams({
      response_type: "id_token",
      client_id: oidcConfig.client_id,
      redirect_uri: redirectUri,
      scope: "openid profile email",
      state: state,
      nonce: _secureRandom(32),
    });
    window.location.href = oidcConfig.authorize_url + "?" + params.toString();
  }

  async function handleOIDCCallback() {
    // Check for OIDC callback in URL hash (implicit flow)
    var hash = window.location.hash;
    if (!hash || hash.indexOf("id_token=") === -1) return false;

    var params = new URLSearchParams(hash.substring(1));
    var idToken = params.get("id_token");
    var state = params.get("state");

    // Verify state — reject if missing or mismatched (CSRF protection)
    var savedState = sessionStorage.getItem("oidc_state");
    sessionStorage.removeItem("oidc_state");
    if (!state || !savedState || state !== savedState) {
      console.error("[SerWebs] OIDC state mismatch or missing");
      return false;
    }

    if (!idToken) return false;

    // Exchange OIDC token for local JWT
    try {
      var resp = await fetch("/auth/oidc/exchange", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: idToken }),
      });
      if (!resp.ok) {
        console.error("[SerWebs] OIDC exchange failed:", resp.status);
        return false;
      }
      var data = await resp.json();
      _storeToken(data.access_token);

      // Clean URL
      window.history.replaceState(null, "", window.location.pathname);
      return true;
    } catch (e) {
      console.error("[SerWebs] OIDC exchange error:", e);
      return false;
    }
  }

  window.SerWebsAuth = {
    getToken: getToken,
    getUser: getUser,
    isLoggedIn: isLoggedIn,
    login: login,
    logout: logout,
    authHeaders: authHeaders,
    fetchAuthConfig: fetchAuthConfig,
    startOIDCLogin: startOIDCLogin,
    handleOIDCCallback: handleOIDCCallback,
  };
})();
