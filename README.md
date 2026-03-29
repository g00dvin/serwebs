# SerWebs — Web Serial Terminal Manager

Lightweight web application for remote management of serial ports (USB-UART adapters) connected to a Linux server. Provides a real-time terminal in the browser with shared session support, REST API for automation, SSH gateway, and multi-backend aggregation.

## Key Features

- **Real-time serial terminal** in the browser via xterm.js
- **Shared sessions** — multiple users see the same data stream from one port
- **Hot-plug detection** — automatic discovery of USB-serial devices via udev
- **Session replay** — new clients get the last 64 KB of session data on connect
- **REST API** — automation endpoints: `/write`, `/write-wait`, `/log`
- **SSH gateway** — access serial ports via SSH without a browser
- **Multi-backend aggregator** — central UI managing multiple SerWebs instances
- **Terminal recording** — asciicast v2 format, compatible with asciinema
- **Audit log** — all user actions logged as JSON Lines with rotation
- **Session logging** — per-port I/O logs with configurable rotation
- **Role-based access** — admin, user, viewer (read-only)
- **Port tags/groups** — organize ports with tags and filter by them
- **Device profiles** — save per-port settings, auto-apply on open
- **Hex mode** — toggle between text and hex dump display
- **Timestamps** — optional per-line timestamps for protocol debugging
- **Macros** — save and execute frequently used commands (stored in browser)
- **Port aliases** — rename ports for readability, aliases persist across restarts
- **Keyboard shortcuts** — Ctrl+L (clear), Ctrl+D (disconnect), Ctrl+H (hex), Ctrl+T (timestamps)
- **Clickable URLs** — links in terminal output are clickable
- **Smart polling** — pauses when tab is hidden, resumes on focus
- **Authentication** — JWT + Basic Auth + OIDC/SSO
- **Docker-ready** — persistent data volume, SSH port exposed
- **Offline-capable** — all JS/CSS libraries bundled locally, no CDN needed
- **No build step** — plain HTML/JS/CSS frontend

## Architecture

```
+-------------+     WebSocket           +----------------------------------+
|  Browser    |<----------------------->|  FastAPI / uvicorn (Python)      |
|  xterm.js   |     /ws/{port_id}       |                                  |
|  Alpine.js  |                         |  WS Manager  ->  Serial Worker   |
+-------------+                         |       |              |           |
                                        |  Ring Buffer    pyserial-asyncio |
+-------------+     REST API            |       |              |           |
|  curl / CI  |<----------------------->|  Port Manager  <->  /dev/ttyUSB* |
+-------------+     /api/ports/...      |       |                          |
                                        |  Audit Logger  Session Logger    |
+-------------+     SSH                 |  Recorder      Profiles / Tags   |
|  ssh client |<----------------------->|                                  |
+-------------+     port 2222           +----------------------------------+
                                                |
                                        +-------+-------+
                                        | Aggregator    |  (optional)
                                        | proxy to      |
                                        | remote        |
                                        | backends      |
                                        +---------------+
```

## Quick Start

### Docker (recommended)

```bash
# Clone and start
git clone <repo-url> && cd serwebs
docker compose up -d

# Open browser: http://localhost:8088
# Login: admin / admin
```

### Local

```bash
pip install -e .
# Or with all optional features:
pip install -e ".[all]"

python -m serwebs
# Open browser: http://localhost:8080
```

## Configuration

All configuration is in `config.toml`. Sections:

### `[server]`

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `0.0.0.0` | Bind address |
| `port` | `8080` | HTTP port |
| `static_dir` | `frontend` | Path to frontend files |

### `[auth]`

| Key | Default | Description |
|-----|---------|-------------|
| `secret_key` | `CHANGE-ME...` | JWT signing key |
| `algorithm` | `HS256` | JWT algorithm |
| `token_expire_minutes` | `480` | Token TTL (8 hours) |

Users are defined as `[[auth.users]]` entries:

```toml
[[auth.users]]
username = "admin"
password_hash = "$2b$12$..."  # bcrypt hash
role = "admin"  # admin | user | viewer
```

Generate password hash: `python -c "import bcrypt; print(bcrypt.hashpw(b'mypass', bcrypt.gensalt()).decode())"`

#### Roles

| Role | Ports | Write | Open/Close | Tags/Profiles | Audit | Recordings |
|------|-------|-------|------------|---------------|-------|------------|
| **admin** | View all | Yes | Yes | Edit | View | Start/Stop/Delete |
| **user** | View all | Yes | No | View | No | Start/Stop |
| **viewer** | View all | No (read-only) | No | View | No | View only |

### `[auth.oidc]` — SSO Integration

```toml
[auth.oidc]
enabled = true
issuer = "https://auth.example.com/application/o/serwebs/"
client_id = "your-client-id"
username_claim = "preferred_username"
role_claim = "groups"
admin_groups = ["serwebs-admin"]
viewer_groups = ["serwebs-viewer"]
default_role = "user"
```

### `[serial]`

| Key | Default | Description |
|-----|---------|-------------|
| `port_patterns` | `["/dev/ttyUSB*", "/dev/ttyACM*"]` | Glob patterns to scan |
| `blacklist_patterns` | `["/dev/ttyS*"]` | Patterns to exclude |
| `ring_buffer_size` | `65536` | Replay buffer per port (bytes) |
| `max_message_size` | `4096` | Max write payload (bytes) |
| `max_clients_per_port` | `10` | WebSocket client limit |
| `max_ports` | `20` | Max simultaneously open ports |

### `[data]`

```toml
[data]
directory = "data"  # Relative to config.toml location
```

All persistent data is stored here: aliases, tags, profiles, audit logs, session logs, recordings. In Docker, mount as a volume:

```yaml
volumes:
  - serwebs-data:/app/data
```

### `[audit]`

```toml
[audit]
enabled = true
max_file_size_mb = 10
max_files = 5
```

Records: login, port open/close, writes, tag/profile changes, recording start/stop.

### `[session_logging]`

```toml
[session_logging]
enabled = true
max_file_size_mb = 50
max_files = 5
timestamp_prefix = true
```

Per-port I/O logs at `data/logs/{port_id}/`.

### `[recordings]`

```toml
[recordings]
enabled = true
max_storage_mb = 500
```

Terminal recordings in asciicast v2 format. Play back with `asciinema play recording.cast`.

### `[ssh]`

```toml
[ssh]
enabled = false
port = 2222
```

SSH gateway allows accessing serial ports without a browser:

```bash
ssh -p 2222 admin@host
# Presents a menu to select an open serial port
# Ctrl+] to disconnect from port
```

Requires: `pip install asyncssh` (included in Docker image).

### `[aggregator]`

```toml
[aggregator]
enabled = false
backends_file = "backends.yaml"
```

Central management of multiple SerWebs instances. See `backends.yaml.example` for configuration format.

## REST API

### Authentication

All API endpoints require authentication. Use Bearer token or Basic Auth:

```bash
# Get token
TOKEN=$(curl -s -X POST http://host:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .access_token)

# Use token
curl -H "Authorization: Bearer $TOKEN" http://host:8080/api/ports

# Or use Basic Auth
curl -u admin:admin http://host:8080/api/ports
```

### Port Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/ports` | List all ports |
| `GET` | `/api/ports/{id}` | Get port details |
| `POST` | `/api/ports/{id}/open` | Open port |
| `POST` | `/api/ports/{id}/close` | Close port |
| `POST` | `/api/ports/{id}/rename` | Set alias |
| `GET` | `/api/ports/{id}/status` | Connection status |

### Automation

```bash
# Write data to port
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": "show version\r\n"}' \
  http://host:8080/api/ports/ttyUSB0/write

# Write and wait for response (with timeout)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": "show version\r\n", "timeout": 3.0}' \
  http://host:8080/api/ports/ttyUSB0/write-wait

# Get session log tail
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/log
```

### Tags & Profiles

```bash
# Set tags
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["router", "rack-1"]}' \
  http://host:8080/api/ports/ttyUSB0/tags

# Save device profile
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile": {"baudrate": 9600, "parity": "none"}}' \
  http://host:8080/api/profiles/ttyUSB0
```

### Recordings

```bash
# Start recording
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/recordings/start

# Stop recording
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/recordings/stop

# List recordings
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/recordings

# Download recording (asciicast v2)
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/recordings/{rec_id} -o recording.cast

# Play with asciinema
asciinema play recording.cast
```

### Audit Log

```bash
# Query audit log (admin only)
curl -H "Authorization: Bearer $TOKEN" \
  "http://host:8080/api/audit?limit=50"

# Filter by event type
curl -H "Authorization: Bearer $TOKEN" \
  "http://host:8080/api/audit?event=port_open&user=admin"
```

### Aggregator

```bash
# List all ports across backends
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/ports

# List backends
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/backends

# Reload backends config
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/backends/reload

# Get WebSocket URL for a remote port (includes backend JWT)
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/ws-url/{backend_name}/{port_id}

# Proxy any API request to a backend
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/proxy/{backend_name}/api/ports
```

Remote ports appear in the UI with a blue backend badge. The browser connects directly to the remote backend's WebSocket using a JWT obtained by the aggregator.

### Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check (no auth) |
| `GET` | `/metrics` | Prometheus-style metrics (admin) |
| `GET` | `/auth/config` | Auth configuration (no auth) |

### WebSocket Protocol

Connect: `ws://host:8080/ws/{port_id}?token=JWT`

Messages (JSON):

```json
// Client -> Server: write data
{"type": "write", "payload": "show version\r\n"}

// Client -> Server: keepalive
{"type": "ping"}

// Server -> Client: serial data (base64)
{"type": "data", "payload": "c2hvdyB2ZXJzaW9u...", "timestamp": "2024-..."}

// Server -> Client: session replay on connect
{"type": "replay", "payload": "base64..."}

// Server -> Client: status change
{"type": "status", "state": "connected|disconnected|device_lost"}

// Server -> Client: error
{"type": "error", "message": "Rate limit exceeded"}
```

## Docker Deployment

```yaml
services:
  serwebs:
    build: .
    ports:
      - "8080:8080"
      - "2222:2222"  # SSH gateway
    volumes:
      - ./config.toml:/app/config.toml:ro
      - serwebs-data:/app/data
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
    group_add:
      - dialout

volumes:
  serwebs-data:
```

### Aggregator-only mode (no local serial devices):

```yaml
services:
  serwebs:
    build: .
    volumes:
      - ./config.toml:/app/config.toml:ro
      - ./backends.yaml:/app/backends.yaml:ro
      - serwebs-data:/app/data
    ports:
      - "8088:8080"

volumes:
  serwebs-data:
```

Enable `[aggregator]` in `config.toml` and configure backends in `backends.yaml`. No `devices` or `privileged` needed.

### All serial devices (privileged):

```yaml
services:
  serwebs:
    build: .
    privileged: true
    volumes:
      - /dev:/dev
      - ./config.toml:/app/config.toml:ro
      - serwebs-data:/app/data
    ports:
      - "8080:8080"
      - "2222:2222"
```

## Production Deployment

### Systemd service

```ini
[Unit]
Description=SerWebs Serial Terminal
After=network.target

[Service]
Type=simple
User=serwebs
Group=dialout
WorkingDirectory=/opt/serwebs
ExecStart=/opt/serwebs/venv/bin/python -m serwebs
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Nginx reverse proxy

```nginx
server {
    listen 443 ssl;
    server_name serwebs.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

## Debugging

```toml
[logging]
level = "debug"   # debug | info | warning | error
format = "text"   # text | json
```

### Common Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| "No serial ports detected" | Wrong patterns or permissions | Check `port_patterns` and `dialout` group |
| "Permission denied" on device | User not in dialout group | `sudo usermod -aG dialout $USER` |
| Port shows "Busy" | Another process holds lock | Check `/var/lock/LCK..ttyUSB0` |
| WebSocket auth fails | Token expired or invalid | Re-login, check `token_expire_minutes` |
| SSH gateway doesn't start | asyncssh not installed | `pip install asyncssh` |
| Aggregator fails | httpx/pyyaml not installed | `pip install pyyaml httpx` |

## Tech Stack

- **Backend**: Python 3.9+, FastAPI, uvicorn, pyserial-asyncio, pyudev
- **Frontend**: Alpine.js 3.14, xterm.js 5.5, vanilla CSS
- **Auth**: python-jose (JWT), bcrypt, OIDC
- **Optional**: asyncssh (SSH gateway), httpx + pyyaml (aggregator)
- **All frontend libraries bundled locally** — no CDN/internet required

## TODO

### High Priority
- [x] Aggregator: WebSocket connectivity to remote terminal sessions (via ws-url API)
- [x] Aggregator: remote port operations via proxy API
- [ ] Aggregator: full CRUD management of remote ports via central UI
- [ ] Webhook/notification system (port connect/disconnect events)
- [ ] Multi-language UI support (i18n)

### Medium Priority
- [ ] Telnet gateway (in addition to SSH)
- [ ] Port auto-open on detection (based on profiles with `auto_open: true`)
- [ ] Session log search/grep in UI
- [ ] Recording playback in browser (embedded asciinema player)
- [ ] User management API (add/remove users without editing config.toml)
- [ ] Port locking — exclusive access mode

### Low Priority
- [ ] Dark/light theme toggle
- [ ] Custom terminal fonts
- [ ] SNMP trap integration for device monitoring
- [ ] Prometheus metrics exporter (`/metrics` in Prometheus format)
- [ ] Mobile-optimized layout improvements
- [ ] Plugin system for custom serial protocols
