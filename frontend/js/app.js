/**
 * Main application — Alpine.js component.
 */
document.addEventListener("alpine:init", function () {
  var Auth = window.SerWebsAuth;
  var Macros = window.SerWebsMacros;

  Alpine.data("app", function () {
    return {
      // Auth state
      loggedIn: false,
      loginForm: { username: "", password: "" },
      loginError: "",
      loginLoading: false,
      user: null,
      authConfig: null,

      // Port state
      ports: [],
      activePort: null,
      portFilter: "",
      loadingPort: null,
      portSettings: {
        baudrate: 115200,
        parity: "none",
        stopbits: 1,
        databits: 8,
        flowcontrol: "none",
      },

      // Terminal state
      terminalConnected: false,
      hexMode: false,
      showTimestamps: false,
      statusMessage: "",
      statusType: "info",

      // Rename
      renamingPort: null,
      renameValue: "",

      // Macros
      macros: [],
      newMacroName: "",
      newMacroCmd: "",
      newMacroCR: true,
      showMacroForm: false,

      // UI toggles
      showSettings: false,

      // Confirm dialog
      confirmAction: null,
      confirmMessage: "",

      // Tags
      allTags: [],
      activeTagFilter: "",
      editingTags: null,
      editingTagsValue: "",

      // Profiles
      editingProfile: null,
      profileForm: {
        baudrate: 115200,
        parity: "none",
        stopbits: 1,
        databits: 8,
        flowcontrol: "none",
        auto_open: false,
      },
      profileExists: false,
      profiles: {},

      // Recordings
      isRecording: false,
      showRecordings: false,
      recordings: [],
      playingRecording: false,
      playingRecordingId: "",
      _playerTerm: null,

      // Aggregator
      _activePortMeta: null,

      // Internal
      _terminal: null,
      _ws: null,
      _pollInterval: null,
      _statusTimer: null,
      _visibilityHandler: null,
      _keyboardHandler: null,

      get filteredPorts() {
        var self = this;
        var result = this.ports;
        if (this.activeTagFilter) {
          result = result.filter(function (p) {
            return p.tags && p.tags.indexOf(self.activeTagFilter) !== -1;
          });
        }
        if (this.portFilter) {
          var q = this.portFilter.toLowerCase();
          result = result.filter(function (p) {
            return p.id.toLowerCase().indexOf(q) !== -1 ||
                   (p.alias && p.alias.toLowerCase().indexOf(q) !== -1) ||
                   (p.description && p.description.toLowerCase().indexOf(q) !== -1) ||
                   (p._backend && p._backend.toLowerCase().indexOf(q) !== -1) ||
                   (p.tags && p.tags.some(function (t) { return t.indexOf(q) !== -1; }));
          });
        }
        return result;
      },

      get isViewer() {
        return this.user && this.user.role === "viewer";
      },

      get isAdmin() {
        return this.user && this.user.role === "admin";
      },

      init: async function () {
        var self = this;

        // Check for OIDC callback first
        var oidcOk = await Auth.handleOIDCCallback();
        if (oidcOk) {
          console.log("[SerWebs] OIDC login successful");
        }

        this.loggedIn = Auth.isLoggedIn();
        this.user = Auth.getUser();
        this.macros = Macros.getMacros();
        this.authConfig = await Auth.fetchAuthConfig();

        if (this.loggedIn) {
          this.fetchPorts();
          this.fetchTags();
          this.fetchProfiles();
          this._startPolling();
        }

        // Pause polling when tab is hidden
        this._visibilityHandler = function () {
          if (document.hidden) {
            self._stopPolling();
          } else if (self.loggedIn) {
            self.fetchPorts();
            self._startPolling();
          }
        };
        document.addEventListener("visibilitychange", this._visibilityHandler);

        // Global keyboard shortcuts
        this._keyboardHandler = function (e) {
          if (!self.activePort) return;
          var termFocused = document.activeElement &&
            document.activeElement.closest && document.activeElement.closest(".xterm");
          if (termFocused) return;

          if (e.ctrlKey && e.key === "l") { e.preventDefault(); self.clearTerminal(); }
          if (e.ctrlKey && e.key === "d") { e.preventDefault(); self.disconnectTerminal(); }
          if (e.ctrlKey && e.key === "h") { e.preventDefault(); self.toggleHexMode(); }
          if (e.ctrlKey && e.key === "t") { e.preventDefault(); self.toggleTimestamps(); }
        };
        document.addEventListener("keydown", this._keyboardHandler);
      },

      destroy: function () {
        if (this._visibilityHandler) {
          document.removeEventListener("visibilitychange", this._visibilityHandler);
        }
        if (this._keyboardHandler) {
          document.removeEventListener("keydown", this._keyboardHandler);
        }
        this._stopPolling();
      },

      // ─── Auth ───
      doLogin: async function () {
        if (this.loginLoading) return;
        this.loginError = "";
        this.loginLoading = true;
        try {
          var payload = await Auth.login(this.loginForm.username, this.loginForm.password);
          this.loggedIn = true;
          this.user = { username: payload.sub, role: payload.role };
          this.fetchPorts();
          this.fetchTags();
          this.fetchProfiles();
          this._startPolling();
        } catch (e) {
          this.loginError = e.message;
        } finally {
          this.loginLoading = false;
        }
      },

      doLogout: function () {
        this.disconnectTerminal();
        Auth.logout();
        this.loggedIn = false;
        this.user = null;
        this.ports = [];
        this.activePort = null;
        this._stopPolling();
        this._updateTitle();
      },

      // ─── Ports ───
      fetchPorts: async function () {
        try {
          var resp = await fetch("/api/ports", { headers: Auth.authHeaders() });
          if (resp.status === 401) { this.doLogout(); return; }
          var localPorts = await resp.json();
          // Mark local ports
          localPorts.forEach(function (p) { p._remote = false; });

          // Fetch aggregator ports (if enabled)
          var remotePorts = [];
          try {
            var aggResp = await fetch("/api/aggregator/ports", { headers: Auth.authHeaders() });
            if (aggResp.ok) {
              var aggData = await aggResp.json();
              remotePorts = (aggData.ports || []).map(function (p) {
                p._remote = true;
                p._backend = p.backend || "";
                p._backendUrl = p.backend_url || "";
                p._originalId = p.original_id || p.id;
                p.tags = p.tags || [];
                return p;
              });
            }
          } catch (e) { /* aggregator not enabled or unavailable — ignore */ }

          this.ports = localPorts.concat(remotePorts);
        } catch (e) {
          console.error("Failed to fetch ports:", e);
        }
      },

      openPort: async function (portId) {
        if (this.loadingPort) return;
        this.loadingPort = portId;
        try {
          // Use profile settings if available
          var settings = Object.assign({}, this.portSettings);
          if (this.profiles[portId]) {
            var prof = this.profiles[portId];
            settings.baudrate = prof.baudrate || settings.baudrate;
            settings.parity = prof.parity || settings.parity;
            settings.stopbits = prof.stopbits || settings.stopbits;
            settings.databits = prof.databits || settings.databits;
            settings.flowcontrol = prof.flowcontrol || settings.flowcontrol;
          }
          var resp = await fetch("/api/ports/" + portId + "/open", {
            method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, Auth.authHeaders()),
            body: JSON.stringify({ settings: settings }),
          });
          if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            this.setStatus(err.detail || "Failed to open port", "error");
            return;
          }
          this.setStatus("Port " + portId + " opened", "success");
          await this.fetchPorts();
          this.connectTerminal(portId);
        } catch (e) {
          this.setStatus(e.message, "error");
        } finally {
          this.loadingPort = null;
        }
      },

      confirmClose: function (portId) {
        var self = this;
        var port = this.ports.find(function (p) { return p.id === portId; });
        var name = (port && port.alias) || portId;
        var clients = (port && port.clients) || 0;
        var msg = "Close port " + name + "?";
        if (clients > 0) {
          msg += "\n" + clients + " client(s) will be disconnected.";
        }
        this.confirmMessage = msg;
        this.confirmAction = function () { self.closePort(portId); };
      },

      doConfirm: function () {
        if (this.confirmAction) this.confirmAction();
        this.confirmAction = null;
        this.confirmMessage = "";
      },

      closePort: async function (portId) {
        try {
          if (this.activePort === portId) this.disconnectTerminal();
          var resp = await fetch("/api/ports/" + portId + "/close", {
            method: "POST",
            headers: Auth.authHeaders(),
          });
          if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            this.setStatus(err.detail || "Failed to close port", "error");
            return;
          }
          this.setStatus("Port " + portId + " closed", "success");
          await this.fetchPorts();
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      // ─── Tags ───
      fetchTags: async function () {
        try {
          var resp = await fetch("/api/tags", { headers: Auth.authHeaders() });
          if (resp.ok) {
            var data = await resp.json();
            this.allTags = data.tags || [];
          }
        } catch (e) { /* ignore */ }
      },

      editTags: function (port) {
        this.editingTags = port;
        this.editingTagsValue = (port.tags || []).join(", ");
      },

      addExistingTag: function (tag) {
        var current = this.editingTagsValue ? this.editingTagsValue.split(",").map(function (t) { return t.trim(); }) : [];
        if (current.indexOf(tag) === -1) {
          current.push(tag);
          this.editingTagsValue = current.join(", ");
        }
      },

      saveTags: async function () {
        if (!this.editingTags) return;
        var portId = this.editingTags.id;
        var tags = this.editingTagsValue.split(",").map(function (t) { return t.trim(); }).filter(Boolean);
        try {
          var resp = await fetch("/api/ports/" + portId + "/tags", {
            method: "PUT",
            headers: Object.assign({ "Content-Type": "application/json" }, Auth.authHeaders()),
            body: JSON.stringify({ tags: tags }),
          });
          if (resp.ok) {
            this.setStatus("Tags updated", "success");
            this.editingTags = null;
            await this.fetchPorts();
            await this.fetchTags();
          }
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      // ─── Profiles ───
      fetchProfiles: async function () {
        try {
          var resp = await fetch("/api/profiles", { headers: Auth.authHeaders() });
          if (resp.ok) {
            var data = await resp.json();
            this.profiles = data.profiles || {};
          }
        } catch (e) { /* ignore */ }
      },

      editProfile: function (port) {
        this.editingProfile = port;
        var existing = this.profiles[port.id];
        if (existing) {
          this.profileForm = Object.assign({
            baudrate: 115200, parity: "none", stopbits: 1,
            databits: 8, flowcontrol: "none", auto_open: false,
          }, existing);
          this.profileExists = true;
        } else {
          this.profileForm = {
            baudrate: port.settings ? port.settings.baudrate : 115200,
            parity: port.settings ? port.settings.parity : "none",
            stopbits: port.settings ? port.settings.stopbits : 1,
            databits: port.settings ? port.settings.databits : 8,
            flowcontrol: port.settings ? port.settings.flowcontrol : "none",
            auto_open: false,
          };
          this.profileExists = false;
        }
      },

      saveProfile: async function () {
        if (!this.editingProfile) return;
        var portId = this.editingProfile.id;
        try {
          var resp = await fetch("/api/profiles/" + portId, {
            method: "PUT",
            headers: Object.assign({ "Content-Type": "application/json" }, Auth.authHeaders()),
            body: JSON.stringify({ profile: this.profileForm }),
          });
          if (resp.ok) {
            this.setStatus("Profile saved", "success");
            this.editingProfile = null;
            await this.fetchProfiles();
          }
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      deleteProfile: async function () {
        if (!this.editingProfile) return;
        var portId = this.editingProfile.id;
        try {
          var resp = await fetch("/api/profiles/" + portId, {
            method: "DELETE",
            headers: Auth.authHeaders(),
          });
          if (resp.ok) {
            this.setStatus("Profile deleted", "success");
            this.editingProfile = null;
            await this.fetchProfiles();
          }
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      // ─── Recordings ───
      _recApiBase: function (portId) {
        // For remote ports, proxy through aggregator to the backend
        var port = this.ports.find(function (p) { return p.id === portId; });
        if (port && port._remote && port._backend && port._originalId) {
          return "/api/aggregator/proxy/" + encodeURIComponent(port._backend) +
                 "/api/ports/" + encodeURIComponent(port._originalId);
        }
        return "/api/ports/" + encodeURIComponent(portId);
      },

      startRecording: async function () {
        if (!this.activePort) return;
        try {
          var resp = await fetch(this._recApiBase(this.activePort) + "/recordings/start", {
            method: "POST",
            headers: Auth.authHeaders(),
          });
          if (resp.ok) {
            this.isRecording = true;
            this.setStatus("Recording started", "success");
          } else {
            var err = await resp.json().catch(function () { return {}; });
            this.setStatus(err.detail || "Failed to start recording", "error");
          }
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      stopRecording: async function () {
        if (!this.activePort) return;
        try {
          var resp = await fetch(this._recApiBase(this.activePort) + "/recordings/stop", {
            method: "POST",
            headers: Auth.authHeaders(),
          });
          if (resp.ok) {
            this.isRecording = false;
            this.setStatus("Recording stopped", "success");
          }
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      fetchRecordings: async function () {
        if (!this.activePort) return;
        try {
          var resp = await fetch(this._recApiBase(this.activePort) + "/recordings", {
            headers: Auth.authHeaders(),
          });
          if (resp.ok) {
            var data = await resp.json();
            this.recordings = data.recordings || [];
          }
        } catch (e) { /* ignore */ }
      },

      playRecording: async function (recId) {
        var self = this;
        if (!this.activePort) return;
        this.playingRecording = true;
        this.playingRecordingId = recId;

        this.$nextTick(function () {
          var container = document.getElementById("recording-player");
          if (!container) return;
          while (container.firstChild) container.removeChild(container.firstChild);

          // Create an xterm instance for playback
          var term = new Terminal({
            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            fontSize: 14,
            theme: { background: "#0d0d0d", foreground: "#e0e0e0" },
            cols: 120,
            rows: 30,
            disableStdin: true,
            cursorBlink: false,
          });
          term.open(container);
          self._playerTerm = term;

          // Fetch and play the recording
          var url = self._recApiBase(self.activePort) + "/recordings/" + encodeURIComponent(recId) + "?inline=true";
          fetch(url, { headers: Auth.authHeaders() })
            .then(function (resp) { return resp.text(); })
            .then(function (text) {
              var lines = text.trim().split("\n");
              if (lines.length === 0) return;

              // Skip header (first line)
              var events = [];
              for (var i = 1; i < lines.length; i++) {
                try {
                  var ev = JSON.parse(lines[i]);
                  if (Array.isArray(ev) && ev.length >= 3) {
                    events.push({ time: ev[0], type: ev[1], data: ev[2] });
                  }
                } catch (e) { /* skip invalid lines */ }
              }

              // Schedule events for playback with actual timing
              var startTime = Date.now();
              events.forEach(function (ev) {
                if (ev.type === "o") {
                  var delay = ev.time * 1000;
                  // Cap delay at 2 seconds to avoid long pauses
                  if (delay > 2000) delay = 2000;
                  setTimeout(function () {
                    if (self._playerTerm) {
                      self._playerTerm.write(ev.data);
                    }
                  }, delay);
                }
              });
            })
            .catch(function (e) {
              term.write("\r\nFailed to load recording: " + e.message + "\r\n");
            });
        });
      },

      stopPlayback: function () {
        if (this._playerTerm) {
          this._playerTerm.dispose();
          this._playerTerm = null;
        }
        this.playingRecording = false;
        this.playingRecordingId = "";
      },

      deleteRecording: async function (recId) {
        if (!this.activePort) return;
        try {
          var resp = await fetch(this._recApiBase(this.activePort) + "/recordings/" + recId, {
            method: "DELETE",
            headers: Auth.authHeaders(),
          });
          if (resp.ok) {
            this.setStatus("Recording deleted", "success");
            await this.fetchRecordings();
          }
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      // ─── Rename ───
      startRename: function (port) {
        var self = this;
        this.renamingPort = port.id;
        this.renameValue = port.alias || "";
        this.$nextTick(function () {
          var input = self.$refs.renameInput;
          if (input) { input.focus(); input.select(); }
        });
      },

      cancelRename: function () {
        this.renamingPort = null;
        this.renameValue = "";
      },

      submitRename: async function (portId) {
        try {
          var resp = await fetch("/api/ports/" + portId + "/rename", {
            method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, Auth.authHeaders()),
            body: JSON.stringify({ alias: this.renameValue }),
          });
          if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            this.setStatus(err.detail || "Failed to rename", "error");
            return;
          }
          this.renamingPort = null;
          this.renameValue = "";
          await this.fetchPorts();
          this.setStatus("Port renamed", "success");
        } catch (e) {
          this.setStatus(e.message, "error");
        }
      },

      portDisplayName: function (port) {
        return port.alias || port.id;
      },

      // ─── Terminal ───
      connectTerminal: function (portId) {
        var self = this;

        var port = this.ports.find(function (p) { return p.id === portId; });
        if (port && port.status !== "open") {
          this.setStatus("Port " + (port.alias || portId) + " is not open", "error");
          return;
        }

        if (this.activePort === portId && this._ws && this._terminal) {
          this._terminal.fit();
          if (this._terminal.term) this._terminal.term.focus();
          return;
        }

        this._cleanupConnection();
        this.activePort = portId;
        this._activePortMeta = port || null;
        this.isRecording = false;
        this._updateTitle();

        this.$nextTick(function () {
          self.$nextTick(function () {
            var container = document.getElementById("terminal-container");
            if (!container || container.offsetWidth === 0) {
              setTimeout(function () { self._initTerminal(portId); }, 100);
              return;
            }
            self._initTerminal(portId);
          });
        });
      },

      _initTerminal: async function (portId) {
        var self = this;
        var container = document.getElementById("terminal-container");
        if (!container) return;

        while (container.firstChild) container.removeChild(container.firstChild);

        this._terminal = new TerminalManager();
        this._terminal.open(container);
        this._terminal.setHexMode(this.hexMode);
        this._terminal.setTimestamps(this.showTimestamps);

        this._terminal.onInput = function (data) {
          if (self.isViewer) return; // Block input for viewers
          if (self._ws) self._ws.send(data);
        };

        setTimeout(function () {
          if (self._terminal) self._terminal.fit();
        }, 50);
        setTimeout(function () {
          if (self._terminal) self._terminal.fit();
        }, 300);

        // Determine WS connection: local or remote backend
        var wsPortId = portId;
        var portMeta = this._activePortMeta;
        this._remoteWsUrl = null;

        if (portMeta && portMeta._remote && portMeta._backend && portMeta._originalId) {
          wsPortId = portMeta._originalId;
          // Fetch WS URL from aggregator (includes backend's auth token)
          try {
            var wsResp = await fetch(
              "/api/aggregator/ws-url/" + encodeURIComponent(portMeta._backend) +
              "/" + encodeURIComponent(portMeta._originalId),
              { headers: Auth.authHeaders() }
            );
            if (wsResp.ok) {
              var wsData = await wsResp.json();
              this._remoteWsUrl = wsData.ws_url || null;
            }
          } catch (e) {
            console.error("Failed to get remote WS URL:", e);
          }
        }

        this._ws = new SerialWebSocket(wsPortId, Auth.getToken());
        this._ws.onData = function (payload) {
          if (self._terminal) self._terminal.writeData(payload);
        };
        this._ws.onReplay = function (payload) {
          if (self._terminal) self._terminal.writeReplay(payload);
        };
        this._ws.onStatus = function (state) {
          if (state === "device_lost") {
            self.setStatus("Device disconnected!", "error");
            self.terminalConnected = false;
          } else if (state === "connected") {
            self.terminalConnected = true;
          } else if (state === "disconnected") {
            self.terminalConnected = false;
          }
          self._updateTitle();
        };
        this._ws.onError = function (msg) {
          self.setStatus(msg, "error");
        };
        this._ws.onOpen = function () {
          self.terminalConnected = true;
          self.setStatus("Connected to " + portId, "success");
          self._updateTitle();
          if (self._terminal) {
            setTimeout(function () { self._terminal.fit(); }, 100);
          }
        };
        this._ws.onClose = function () {
          self.terminalConnected = false;
          self._updateTitle();
        };
        this._ws.connect(this._remoteWsUrl);
      },

      _cleanupConnection: function () {
        if (this._ws) {
          this._ws.disconnect();
          this._ws = null;
        }
        if (this._terminal) {
          this._terminal.dispose();
          this._terminal = null;
        }
        this.terminalConnected = false;
        this.isRecording = false;
        var container = document.getElementById("terminal-container");
        if (container) { while (container.firstChild) container.removeChild(container.firstChild); }
      },

      disconnectTerminal: function () {
        this._cleanupConnection();
        this.activePort = null;
        this._updateTitle();
      },

      toggleHexMode: function () {
        this.hexMode = !this.hexMode;
        if (this._terminal) this._terminal.setHexMode(this.hexMode);
      },

      toggleTimestamps: function () {
        this.showTimestamps = !this.showTimestamps;
        if (this._terminal) this._terminal.setTimestamps(this.showTimestamps);
      },

      clearTerminal: function () {
        if (this._terminal) this._terminal.clear();
      },

      saveLog: function () {
        if (this._terminal) {
          this._terminal.saveLog((this.activePort || "serial") + "_" + Date.now() + ".log");
        }
      },

      // ─── Macros ───
      doAddMacro: function () {
        if (!this.newMacroName || !this.newMacroCmd) return;
        this.macros = Macros.addMacro(this.newMacroName, this.newMacroCmd, this.newMacroCR);
        this.newMacroName = "";
        this.newMacroCmd = "";
        this.newMacroCR = true;
        this.showMacroForm = false;
      },

      doDeleteMacro: function (index) {
        this.macros = Macros.deleteMacro(index);
      },

      doExecuteMacro: function (index) {
        var self = this;
        if (this._ws) {
          Macros.executeMacro(index, function (data) { self._ws.send(data); });
        }
      },

      // ─── Status ───
      setStatus: function (message, type) {
        if (this._statusTimer) clearTimeout(this._statusTimer);
        this.statusMessage = message;
        this.statusType = type || "info";
        var self = this;
        this._statusTimer = setTimeout(function () { self.statusMessage = ""; }, 5000);
      },

      // ─── Page title ───
      _updateTitle: function () {
        var base = "SerWebs";
        if (this.activePort) {
          var port = this.ports.find(function (p) { return p.id === this.activePort; }.bind(this));
          var name = (port && port.alias) || this.activePort;
          var dot = this.terminalConnected ? "●" : "○";
          document.title = dot + " " + name + " — " + base;
        } else {
          document.title = base + " — Serial Terminal";
        }
      },

      // ─── Helpers ───
      portStatusClass: function (status) {
        return { free: "status-free", open: "status-open", busy: "status-busy", unavailable: "status-unavailable" }[status] || "";
      },

      portStatusLabel: function (status) {
        return { free: "Free", open: "Open", busy: "Busy", unavailable: "Lost" }[status] || status;
      },

      _startPolling: function () {
        this._stopPolling();
        var self = this;
        this._pollInterval = setInterval(function () { self.fetchPorts(); }, 5000);
      },

      _stopPolling: function () {
        if (this._pollInterval) {
          clearInterval(this._pollInterval);
          this._pollInterval = null;
        }
      },
    };
  });
});
