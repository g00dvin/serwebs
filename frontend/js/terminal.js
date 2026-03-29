/**
 * xterm.js wrapper with hex mode, timestamps, and write throttling.
 */
(function () {
  var MAX_LOG_BYTES = 1024 * 1024; // 1 MB log buffer cap
  var WRITE_THROTTLE_MS = 10;       // min interval between writes
  var MAX_CHUNK_BYTES = 16384;      // max bytes per write (16 KB)

  function TerminalManager() {
    this.term = null;
    this.fitAddon = null;
    this._webLinksAddon = null;
    this._hexMode = false;
    this._showTimestamps = false;
    this._logBuffer = [];
    this._logSize = 0;
    this._resizeHandler = null;
    this._writeQueue = [];
    this._writePending = false;
    this._paused = false;
    this._decoder = new TextDecoder("utf-8", { fatal: false });
    this.onInput = null;
  }

  TerminalManager.prototype.open = function (container) {
    var self = this;
    this.term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
      scrollback: 10000,
      fastScrollModifier: "shift",
      theme: {
        background: "#1a1a2e",
        foreground: "#e0e0e0",
        cursor: "#00d4ff",
        selectionBackground: "#3a3a5e",
        selectionForeground: "#ffffff",
        black: "#1a1a2e",
        red: "#ff6b6b",
        green: "#51cf66",
        yellow: "#ffd43b",
        blue: "#339af0",
        magenta: "#cc5de8",
        cyan: "#22b8cf",
        white: "#e0e0e0",
        brightBlack: "#555577",
        brightRed: "#ff8787",
        brightGreen: "#69db7c",
        brightYellow: "#ffe066",
        brightBlue: "#5c7cfa",
        brightMagenta: "#da77f2",
        brightCyan: "#3bc9db",
        brightWhite: "#ffffff",
      },
    });

    this.fitAddon = new FitAddon.FitAddon();
    this.term.loadAddon(this.fitAddon);

    // Clickable URLs in terminal output
    if (window.WebLinksAddon) {
      this._webLinksAddon = new WebLinksAddon.WebLinksAddon();
      this.term.loadAddon(this._webLinksAddon);
    }

    this.term.open(container);

    setTimeout(function () { self.fit(); }, 50);
    setTimeout(function () { self.fit(); }, 200);

    this.term.onData(function (data) {
      if (self.onInput) self.onInput(data);
    });

    this._resizeHandler = function () { self.fit(); };
    window.addEventListener("resize", this._resizeHandler);
    return this;
  };

  TerminalManager.prototype.fit = function () {
    if (this.fitAddon) {
      try { this.fitAddon.fit(); } catch (e) {}
    }
  };

  TerminalManager.prototype.writeData = function (base64Data) {
    if (this._paused || !this.term) return;

    var bytes = Uint8Array.from(atob(base64Data), function (c) { return c.charCodeAt(0); });

    if (bytes.length > MAX_CHUNK_BYTES) {
      bytes = bytes.slice(0, MAX_CHUNK_BYTES);
    }

    var text = this._decoder.decode(bytes, { stream: true });

    this._logSize += text.length;
    this._logBuffer.push(text);
    if (this._logSize > MAX_LOG_BYTES) {
      while (this._logSize > MAX_LOG_BYTES / 2 && this._logBuffer.length > 1) {
        this._logSize -= this._logBuffer.shift().length;
      }
    }

    this._writeQueue.push({ bytes: bytes, text: text });
    this._flushQueue();
  };

  TerminalManager.prototype._flushQueue = function () {
    if (this._writePending || this._writeQueue.length === 0 || !this.term) return;
    var self = this;
    this._writePending = true;

    var item = this._writeQueue.shift();
    if (this._hexMode) {
      this.term.write(this._toHex(item.bytes));
    } else if (this._showTimestamps) {
      var ts = new Date().toTimeString().slice(0, 8);
      var lines = item.text.split("\n");
      for (var i = 0; i < lines.length; i++) {
        if (lines[i].length > 0) {
          this.term.write("\x1b[90m[" + ts + "]\x1b[0m " + lines[i] + "\n");
        }
      }
    } else {
      this.term.write(item.text);
    }

    if (this._writeQueue.length > 50) {
      this._writeQueue = this._writeQueue.slice(-10);
    }

    setTimeout(function () {
      self._writePending = false;
      self._flushQueue();
    }, WRITE_THROTTLE_MS);
  };

  TerminalManager.prototype.setPaused = function (paused) {
    this._paused = paused;
    if (!paused) this._flushQueue();
  };

  TerminalManager.prototype.writeReplay = function (base64Data) {
    var bytes = Uint8Array.from(atob(base64Data), function (c) { return c.charCodeAt(0); });
    var text = this._decoder.decode(bytes);

    this.term.write("\x1b[90m--- Session replay ---\x1b[0m\r\n");
    if (this._hexMode) {
      this.term.write(this._toHex(bytes));
    } else {
      this.term.write(text);
    }
    this.term.write("\x1b[90m--- Live ---\x1b[0m\r\n");
  };

  TerminalManager.prototype.setHexMode = function (enabled) {
    this._hexMode = enabled;
  };

  TerminalManager.prototype.setTimestamps = function (enabled) {
    this._showTimestamps = enabled;
  };

  TerminalManager.prototype.clear = function () {
    if (this.term) {
      this.term.clear();
      this.term.reset();
      this._logBuffer = [];
      this._logSize = 0;
      this._writeQueue = [];
      this._writePending = false;
      this._paused = false;
    }
  };

  TerminalManager.prototype.saveLog = function (filename) {
    var content = this._logBuffer.join("");
    var blob = new Blob([content], { type: "text/plain" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename || "serial_" + Date.now() + ".log";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  TerminalManager.prototype.dispose = function () {
    if (this._resizeHandler) {
      window.removeEventListener("resize", this._resizeHandler);
      this._resizeHandler = null;
    }
    this._webLinksAddon = null;
    this.fitAddon = null;
    if (this.term) {
      try {
        this.term.dispose();
      } catch (e) {
        console.warn("[SerWebs] terminal dispose error (ignored):", e.message);
      }
      this.term = null;
    }
  };

  TerminalManager.prototype._toHex = function (bytes) {
    var result = "";
    for (var i = 0; i < bytes.length; i += 16) {
      var chunk = bytes.slice(i, i + 16);
      var hex = Array.from(chunk).map(function (b) { return b.toString(16).padStart(2, "0"); }).join(" ");
      var ascii = Array.from(chunk).map(function (b) { return (b >= 32 && b < 127) ? String.fromCharCode(b) : "."; }).join("");
      var offset = i.toString(16).padStart(8, "0");
      result += "\x1b[36m" + offset + "\x1b[0m  " + hex.padEnd(47) + "  \x1b[33m" + ascii + "\x1b[0m\r\n";
    }
    return result;
  };

  window.TerminalManager = TerminalManager;
})();
