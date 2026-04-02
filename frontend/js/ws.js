/**
 * WebSocket client with auto-reconnect and exponential backoff.
 */
(function () {
  function SerialWebSocket(portId, token) {
    this.portId = portId;
    this.token = token;
    this._ws = null;
    this._reconnectAttempt = 0;
    this._maxDelay = 30000;
    this._shouldReconnect = true;
    this._pingInterval = null;

    this.onData = null;
    this.onReplay = null;
    this.onStatus = null;
    this.onError = null;
    this.onOpen = null;
    this.onClose = null;
  }

  /**
   * Connect to a WebSocket.
   * @param {string} [overrideUrl] — full ws:// URL for remote backend connections
   *   (remote URLs still carry token in query param — backend-to-backend only).
   */
  SerialWebSocket.prototype.connect = function (overrideUrl) {
    var self = this;
    var url;
    if (overrideUrl) {
      // Remote backend — token is already embedded by aggregator (backend-to-backend)
      url = overrideUrl;
      this._authViaMessage = false;
    } else {
      // Local — connect without token in URL, send auth as first message
      var proto = location.protocol === "https:" ? "wss:" : "ws:";
      url = proto + "//" + location.host + "/ws/" + this.portId;
      this._authViaMessage = true;
    }
    this._remoteUrl = overrideUrl || null;

    this._ws = new WebSocket(url);

    this._ws.onopen = function () {
      // Send token as first message for local connections (avoids token in URL/logs)
      if (self._authViaMessage && self.token) {
        self._ws.send(JSON.stringify({ type: "auth", token: self.token }));
      }
      self._reconnectAttempt = 0;
      self._startPing();
      if (self.onOpen) self.onOpen();
    };

    this._ws.onmessage = function (event) {
      try {
        var msg = JSON.parse(event.data);
        switch (msg.type) {
          case "data":
            if (self.onData) self.onData(msg.payload, msg.timestamp);
            break;
          case "replay":
            if (self.onReplay) self.onReplay(msg.payload);
            break;
          case "status":
            if (self.onStatus) self.onStatus(msg.state);
            break;
          case "error":
            if (self.onError) self.onError(msg.message);
            break;
          case "pong":
            break;
        }
      } catch (e) {
        console.error("Failed to parse WS message:", e);
      }
    };

    this._ws.onclose = function (event) {
      self._stopPing();
      console.log("[SerWebs WS] closed, code:", event.code, "reason:", event.reason);
      // Don't reconnect on server rejection codes or direct close without accept
      if (event.code >= 4000 || event.code === 1006 || event.code === 1008) {
        console.log("[SerWebs WS] stopping reconnect (code " + event.code + ")");
        self._shouldReconnect = false;
      }
      if (self.onClose) self.onClose();
      if (self._shouldReconnect) self._reconnect();
    };

    this._ws.onerror = function () {
      console.log("[SerWebs WS] error event");
    };
  };

  SerialWebSocket.prototype.send = function (payload) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ type: "write", payload: payload }));
    }
  };

  SerialWebSocket.prototype.disconnect = function () {
    this._shouldReconnect = false;
    this._stopPing();
    if (this._ws) {
      this._ws.close();
      this._ws = null;
    }
  };

  SerialWebSocket.prototype._reconnect = function () {
    var self = this;
    var delay = Math.min(1000 * Math.pow(2, this._reconnectAttempt), this._maxDelay);
    this._reconnectAttempt++;
    setTimeout(function () {
      if (self._shouldReconnect) self.connect(self._remoteUrl);
    }, delay);
  };

  SerialWebSocket.prototype._startPing = function () {
    var self = this;
    this._pingInterval = setInterval(function () {
      if (self._ws && self._ws.readyState === WebSocket.OPEN) {
        self._ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 30000);
  };

  SerialWebSocket.prototype._stopPing = function () {
    if (this._pingInterval) {
      clearInterval(this._pingInterval);
      this._pingInterval = null;
    }
  };

  window.SerialWebSocket = SerialWebSocket;
})();
