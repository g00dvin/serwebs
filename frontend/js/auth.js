/**
 * Auth module — JWT token management + OIDC support.
 */
(function () {
  var TOKEN_KEY = "serwebs_token";
  var USER_KEY = "serwebs_user";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function getUser() {
    var raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  }

  function isLoggedIn() {
    var token = getToken();
    if (!token) return false;
    try {
      var payload = JSON.parse(atob(token.split(".")[1]));
      return payload.exp * 1000 > Date.now();
    } catch (e) {
      return false;
    }
  }

  function _storeToken(accessToken) {
    localStorage.setItem(TOKEN_KEY, accessToken);
    var payload = JSON.parse(atob(accessToken.split(".")[1]));
    localStorage.setItem(USER_KEY, JSON.stringify({ username: payload.sub, role: payload.role }));
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
    var state = Math.random().toString(36).slice(2);
    sessionStorage.setItem("oidc_state", state);

    var params = new URLSearchParams({
      response_type: "id_token",
      client_id: oidcConfig.client_id,
      redirect_uri: redirectUri,
      scope: "openid profile email",
      state: state,
      nonce: Math.random().toString(36).slice(2),
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

    // Verify state
    var savedState = sessionStorage.getItem("oidc_state");
    if (state && savedState && state !== savedState) {
      console.error("[SerWebs] OIDC state mismatch");
      return false;
    }
    sessionStorage.removeItem("oidc_state");

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
