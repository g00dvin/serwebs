# SerWebs — Web Serial Terminal Manager

Lightweight web application for remote management of serial ports (USB-UART adapters) connected to a Linux server. Provides a real-time terminal in the browser with shared session support, REST API for automation, SSH/Telnet gateways, enterprise authentication (LDAP/RADIUS/TACACS+), alerting, syslog integration, and multi-backend aggregation.

## Key Features

- **Real-time serial terminal** in the browser via xterm.js
- **Shared sessions** — multiple users see the same data stream from one port
- **Hot-plug detection** — automatic discovery of USB-serial devices via udev
- **Session replay** — new clients get the last 64 KB of session data on connect
- **REST API** — automation endpoints: `/write`, `/write-wait`, `/log`
- **SSH gateway** — access serial ports via SSH without a browser
- **Telnet gateway** — lightweight Telnet access for environments without SSH
- **Multi-backend aggregator** — central UI managing multiple SerWebs instances with full CRUD
- **Terminal recording** — asciicast v2 format with **in-browser playback**
- **Audit log** — all user actions logged as JSON Lines with rotation
- **Session logging** — per-port I/O logs with configurable rotation
- **Role-based access** — admin, user, viewer (read-only)
- **Enterprise auth** — LDAP/Active Directory, RADIUS, TACACS+ (in addition to local users and OIDC)
- **User management API** — add/remove users without editing config.toml
- **Port locking** — exclusive access mode, only lock holder can write
- **Webhook/email alerting** — real-time notifications for port events
- **Syslog forwarding** — RFC 3164/5424 syslog output for SIEM integration
- **Port tags/groups** — organize ports with tags and filter by them
- **Device profiles** — save per-port settings, auto-apply on open
- **Hex mode** — toggle between text and hex dump display
- **Timestamps** — optional per-line timestamps for protocol debugging
- **Macros** — save and execute frequently used commands (stored in browser)
- **Port aliases** — rename ports for readability, aliases persist across restarts
- **Keyboard shortcuts** — Ctrl+L (clear), Ctrl+D (disconnect), Ctrl+H (hex), Ctrl+T (timestamps)
- **Clickable URLs** — links in terminal output are clickable
- **Smart polling** — pauses when tab is hidden, resumes on focus
- **Authentication** — JWT + Basic Auth + OIDC/SSO + LDAP + RADIUS + TACACS+
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
+-------------+     SSH (2222)          |  Recorder      Profiles / Tags   |
|  ssh client |<----------------------->|  Alerting      Syslog Forwarder  |
+-------------+                         |  User Mgmt     Port Locking      |
                                        +----------------------------------+
+-------------+     Telnet (2323)               |
|telnet client|<----------------------->+-------+-------+
+-------------+                         | Aggregator    |  (optional)
                                        | CRUD proxy to |
+-------------+     Auth backends       | remote        |
| LDAP/AD     |<- - - - - - - - - - - -| backends      |
| RADIUS      |                         +-------+-------+
| TACACS+     |                                 |
+-------------+                         +-------+-------+
                                        | Webhook/Email |
                                        | Syslog/SIEM   |
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

### `[auth.ldap]` — LDAP / Active Directory

```toml
[auth.ldap]
enabled = true
url = "ldap://ldap.example.com:389"
bind_dn = "cn=serwebs,ou=services,dc=example,dc=com"
bind_password = "service-password"
user_base_dn = "ou=users,dc=example,dc=com"
user_filter = "(uid={username})"          # {username} is replaced at runtime
username_attribute = "uid"                # or "sAMAccountName" for AD
group_base_dn = "ou=groups,dc=example,dc=com"
group_filter = "(member={user_dn})"       # {user_dn} is replaced at runtime
admin_groups = ["serwebs-admin"]
viewer_groups = ["serwebs-viewer"]
default_role = "user"
use_ssl = false       # ldaps://
start_tls = false     # STARTTLS over ldap://
ca_cert_file = ""     # CA certificate for TLS verification
```

Authentication flow: bind with service account → search for user → bind as user (password verify) → query groups for role mapping.

Requires: `pip install ldap3` (included in `pip install -e ".[ldap]"`).

### `[auth.radius]` — RADIUS

```toml
[auth.radius]
enabled = true
server = "radius.example.com"
port = 1812
secret = "radius-shared-secret"
timeout = 5
retries = 3
nas_identifier = "serwebs"
admin_filter_id = "serwebs-admin"    # Filter-Id attribute value for admin role
viewer_filter_id = "serwebs-viewer"  # Filter-Id attribute value for viewer role
default_role = "user"
```

Role mapping uses the RADIUS `Filter-Id` attribute returned in Access-Accept. Configure your RADIUS server to return `Filter-Id = serwebs-admin` for admin users.

Requires: `pip install pyrad` (included in `pip install -e ".[radius]"`).

### `[auth.tacacs]` — TACACS+

```toml
[auth.tacacs]
enabled = true
server = "tacacs.example.com"
port = 49
secret = "tacacs-shared-secret"
timeout = 5
service = "serwebs"
admin_priv_lvl = 15    # privilege level >= this → admin role
viewer_priv_lvl = 1    # privilege level <= this → viewer role
default_role = "user"
```

After authentication, SerWebs performs TACACS+ authorization to retrieve the user's `priv-lvl`. Privilege level 15 maps to admin, level 1 to viewer, everything in between to user.

Requires: `pip install tacacs_plus` (included in `pip install -e ".[tacacs]"`).

### Authentication Priority

When a user logs in, backends are tried in order:

1. **Local users** (`[[auth.users]]` in config.toml)
2. **Runtime users** (managed via User Management API, stored in `data/users.json`)
3. **LDAP** (if `[auth.ldap] enabled = true`)
4. **RADIUS** (if `[auth.radius] enabled = true`)
5. **TACACS+** (if `[auth.tacacs] enabled = true`)
6. **OIDC** (handled separately via token exchange)

The first successful match wins. Multiple backends can be enabled simultaneously.

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

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable SSH gateway |
| `port` | `2222` | SSH listen port |
| `host_key_file` | `""` | Path to SSH host key (auto-generated if empty) |

```toml
[ssh]
enabled = true
port = 2222
# host_key_file = "/path/to/ssh_host_key"  # optional, auto-generated if omitted
```

Requires: `pip install asyncssh` (included in Docker image and `pip install -e ".[ssh]"`).

#### How it works

The SSH gateway authenticates users against the same user database as the web UI (`[[auth.users]]` in config.toml). After login, it presents an interactive menu of currently open serial ports. Selecting a port bridges the SSH session directly to the serial I/O stream, with session replay (last 64 KB) delivered on connect.

- **Viewer** role users can observe port output but cannot send data
- **Ctrl+]** disconnects from the current port and returns to the menu
- **Ctrl+C** or **q** at the menu exits the session
- Session timeout: 2 minutes of inactivity at the menu

If no `host_key_file` is specified, an RSA-2048 host key is auto-generated on first start and saved alongside config.toml (`ssh_host_key` / `ssh_host_key.pub`).

#### Usage examples

```bash
# Basic connection
ssh -p 2222 admin@serwebs-host

# With non-standard SSH key
ssh -p 2222 -i ~/.ssh/my_key admin@serwebs-host

# Suppress host key prompt on first connect
ssh -p 2222 -o StrictHostKeyChecking=accept-new admin@serwebs-host

# One-liner for CI/scripts (non-interactive — opens port 1 automatically)
echo "1" | ssh -p 2222 -o StrictHostKeyChecking=accept-new admin@serwebs-host
```

Example session:

```
$ ssh -p 2222 admin@10.0.0.1

Welcome to SerWebs SSH Gateway, admin!
Role: admin

Available ports:
  1. Router Console (Cisco router) [115200 baud]
  2. Switch Mgmt (HP switch) [9600 baud]
  q. Quit

Select port: 1

Connected to ttyUSB0. Press Ctrl+] to disconnect.

Router>show version
Cisco IOS Software, ...
Router>
^]
Disconnected from port.

Available ports:
  1. Router Console (Cisco router) [115200 baud]
  2. Switch Mgmt (HP switch) [9600 baud]
  q. Quit

Select port: q
Goodbye!
Connection to 10.0.0.1 closed.
```

#### Docker deployment with SSH

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
```

Make sure `[ssh] enabled = true` in config.toml.

### `[aggregator]`

```toml
[aggregator]
enabled = false
backends_file = "backends.yaml"
```

Central management of multiple SerWebs instances. See `backends.yaml.example` for configuration format. The aggregator now supports full CRUD for backend management and remote port operations (open/close/rename/write) via the central UI.

### `[telnet]`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable Telnet gateway |
| `port` | `2323` | Telnet listen port |
| `timeout` | `120` | Inactivity timeout at menu (seconds) |

```toml
[telnet]
enabled = true
port = 2323
timeout = 120
```

The Telnet gateway provides the same interactive serial port access as the SSH gateway but over an unencrypted Telnet connection. Useful for environments where SSH clients are not available (e.g., legacy management stations, embedded systems, network boot environments).

**Usage:**

```bash
telnet serwebs-host 2323
```

The session flow is identical to SSH: authenticate with username/password → select a port → bridge I/O. Press **Ctrl+]** to disconnect from a port, **q** to quit.

**Security note:** Telnet transmits credentials in plaintext. Use only on trusted/isolated management networks, or behind a VPN/SSH tunnel.

### `[alerting]`

```toml
[alerting]
enabled = true

# Webhook (Slack, Teams, PagerDuty, generic)
webhook_url = "https://hooks.slack.com/services/T.../B.../xxx"
# webhook_headers = { "X-Custom" = "value" }  # optional extra headers

# Email (SMTP)
smtp_host = "smtp.gmail.com"
smtp_port = 587
smtp_use_tls = true
smtp_username = "alerts@example.com"
smtp_password = "app-password"
smtp_from = "serwebs@example.com"
smtp_to = ["ops-team@example.com", "noc@example.com"]

# Events to alert on (default list shown)
events = [
    "port_open", "port_close", "device_lost",
    "login_failed", "ws_connect", "recording_start",
]
```

Alerts are fired asynchronously via the audit log pipeline. Each audit event is checked against the `events` list — matching events are sent to the configured webhook and/or email recipients.

**Webhook payload** (JSON POST):

```json
{
  "event": "device_lost",
  "timestamp": "2025-01-15T10:30:00Z",
  "user": "admin",
  "port_id": "ttyUSB0"
}
```

**Supported integrations:** Slack (incoming webhook), Microsoft Teams (webhook connector), PagerDuty (Events API v2), OpsGenie, any HTTP endpoint accepting JSON POST.

### `[syslog]`

```toml
[syslog]
enabled = true
host = "syslog.example.com"
port = 514
protocol = "udp"     # udp or tcp
facility = "local0"  # kern, user, daemon, auth, local0-local7
format = "rfc5424"   # rfc3164 or rfc5424
```

All audit events are forwarded to the configured syslog server. This integrates SerWebs with enterprise SIEM platforms (Splunk, ELK, Graylog, QRadar).

- **RFC 5424** (default): structured data with `[serwebs@0 user="admin" port_id="ttyUSB0"]`
- **RFC 3164**: traditional BSD syslog format
- Severity mapping: `*_failed` events → warning (4), all others → info (6)

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

# Play with asciinema (CLI)
asciinema play recording.cast

# Play in browser — click "Play" button in the Recordings panel
# Or use inline mode via API:
curl -H "Authorization: Bearer $TOKEN" \
  "http://host:8080/api/ports/ttyUSB0/recordings/{rec_id}?inline=true"
```

Recordings can be played back directly in the browser using the built-in xterm.js player. Click the **Play** button next to any recording in the Recordings panel — the asciicast is fetched and replayed with original timing (capped at 2s per pause) in a modal terminal window.

### Audit Log

```bash
# Query audit log (admin only)
curl -H "Authorization: Bearer $TOKEN" \
  "http://host:8080/api/audit?limit=50"

# Filter by event type
curl -H "Authorization: Bearer $TOKEN" \
  "http://host:8080/api/audit?event=port_open&user=admin"
```

### User Management API

Manage users at runtime without editing `config.toml`. Users created via this API are stored in `data/users.json`.

```bash
# List all users (config.toml + API-managed)
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/users

# Create a new user
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "newuser", "password": "securepass123", "role": "user"}' \
  http://host:8080/api/users

# Update user role or password
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "admin"}' \
  http://host:8080/api/users/newuser

# Change password
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": "newpassword456"}' \
  http://host:8080/api/users/newuser

# Delete a user
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/users/newuser
```

**Note:** Users defined in `config.toml` (`[[auth.users]]`) cannot be modified or deleted via the API. The response includes a `source` field (`"config"` or `"api"`) to distinguish them.

### Port Locking

Lock a port for exclusive write access. When locked, only the lock holder can send data — other users see a "port is locked" error on write attempts. Viewers and read operations are not affected.

```bash
# Lock a port
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/lock

# Check lock status
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/lock
# Response: {"locked": true, "locked_by": "admin", "locked_at": "2025-01-15T10:30:00Z"}

# Unlock a port (only lock holder or admin)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/ports/ttyUSB0/unlock
```

Port locks apply to both WebSocket writes and REST API writes (`/api/ports/{id}/write`). Locks persist across reconnects (stored in `data/port_locks.json`) and must be explicitly released.

### Aggregator

```bash
# List all ports across backends
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/ports

# List backends
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/backends

# Add a new backend
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "lab-rack-3", "url": "http://192.168.1.12:8080", "token": "eyJ..."}' \
  http://host:8080/api/aggregator/backends

# Update a backend
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "http://192.168.1.13:8080"}' \
  http://host:8080/api/aggregator/backends/lab-rack-3

# Remove a backend
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/backends/lab-rack-3

# Reload backends config from YAML
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/backends/reload

# Remote port management (open/close/rename/write on remote backends)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/ports/{backend_name}/{port_id}/open

curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/ports/{backend_name}/{port_id}/close

curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"alias": "Core Router"}' \
  http://host:8080/api/aggregator/ports/{backend_name}/{port_id}/rename

curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": "show version\r\n"}' \
  http://host:8080/api/aggregator/ports/{backend_name}/{port_id}/write

# Get WebSocket URL for a remote port (includes backend JWT)
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/ws-url/{backend_name}/{port_id}

# Proxy any API request to a backend
curl -H "Authorization: Bearer $TOKEN" \
  http://host:8080/api/aggregator/proxy/{backend_name}/api/ports
```

Backend CRUD operations are persisted to `backends.yaml`. Remote ports appear in the UI with a blue backend badge. The browser connects directly to the remote backend's WebSocket using a JWT obtained by the aggregator.

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
      - "2323:2323"  # Telnet gateway
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

## Frontend Security: Subresource Integrity (SRI)

All vendor JavaScript and CSS files in `frontend/vendor/` are loaded with [Subresource Integrity](https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity) hashes. The browser verifies each file's SHA-384 hash before executing it — if even one byte is changed (accidentally or maliciously), the browser refuses to load the file.

In `index.html` this looks like:

```html
<script src="/vendor/xterm.min.js"
        integrity="sha384-J4qzUjBl1Fxy..." crossorigin="anonymous"></script>
```

### How it works

1. At build/release time, a SHA-384 hash is computed for each vendor file
2. The hash is embedded in the `integrity` attribute of the `<script>` or `<link>` tag
3. When a browser downloads the file, it computes the hash independently
4. If the computed hash does not match, the file is **blocked** — the page will break visibly rather than run tampered code

This protects against:
- Accidental corruption of vendor files during copy/deploy
- Malicious file replacement on the server (supply-chain attack)
- Integrity verification when files are served through CDN or cache proxies

### Updating vendor files

When you update any file in `frontend/vendor/`, the SRI hash in `index.html` must be updated too. Otherwise the browser will refuse to load the file and the UI will not work.

**Step-by-step:**

```bash
# 1. Replace the vendor file with the new version
cp ~/downloads/xterm-5.6.0/xterm.min.js frontend/vendor/xterm.min.js

# 2. Generate the new SRI hash
openssl dgst -sha384 -binary frontend/vendor/xterm.min.js | openssl base64 -A
# Output: J4qzUjBl1FxyLsl/kQPQIOeINsmp17OHYXDOMpMxlKX53ZfYsL+aWHpgArvOuof9

# 3. Update index.html — replace the old integrity value
#    integrity="sha384-<PASTE_NEW_HASH_HERE>"
```

To regenerate all hashes at once:

```bash
for f in frontend/vendor/*.js frontend/vendor/*.css; do
  hash=$(openssl dgst -sha384 -binary "$f" | openssl base64 -A)
  echo "$(basename $f): sha384-$hash"
done
```

Then update each `integrity="sha384-..."` attribute in `frontend/index.html` with the corresponding hash.

**Important:** if you skip this step after updating a vendor file, the browser will show a blank page. Check the browser console for `Failed to find a valid digest` errors — this means the SRI hash is outdated.

### Files covered by SRI

| File | Purpose |
|------|---------|
| `vendor/xterm.min.js` | Terminal emulator |
| `vendor/addon-fit.min.js` | Terminal auto-resize |
| `vendor/addon-web-links.min.js` | Clickable URLs in terminal |
| `vendor/alpine.min.js` | UI reactivity framework |
| `vendor/alpine-collapse.min.js` | Collapsible panels |
| `vendor/xterm.min.css` | Terminal styles |

Application files (`js/app.js`, `js/auth.js`, etc.) do not have SRI because they are developed in-tree and change frequently. They are protected by a same-origin CSP (`script-src 'self' 'unsafe-eval'`): scripts may only load from the same origin, and `unsafe-eval` is enabled because Alpine.js evaluates directive expressions at runtime.

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
| Telnet gateway doesn't start | Check `[telnet] enabled` | Set `enabled = true` in config.toml |
| LDAP auth fails | ldap3 not installed | `pip install ldap3` |
| RADIUS auth fails | pyrad not installed | `pip install pyrad` |
| TACACS+ auth fails | tacacs_plus not installed | `pip install tacacs_plus` |
| Aggregator fails | httpx/pyyaml not installed | `pip install pyyaml httpx` |
| Port locked, can't write | Another user holds the lock | Check `/api/ports/{id}/lock`, admin can force unlock |
| Alerts not sending | webhook_url or smtp_host not set | Check `[alerting]` config |

## Tech Stack

- **Backend**: Python 3.9+, FastAPI, uvicorn, pyserial-asyncio, pyudev
- **Frontend**: Alpine.js 3.14, xterm.js 5.5, vanilla CSS
- **Auth**: python-jose (JWT), bcrypt, OIDC, LDAP (ldap3), RADIUS (pyrad), TACACS+ (tacacs_plus)
- **Optional**: asyncssh (SSH gateway), httpx + pyyaml (aggregator), ldap3, pyrad, tacacs_plus
- **Alerting**: Webhook (Slack/Teams/PagerDuty), SMTP email
- **Monitoring**: Syslog forwarding (RFC 3164/5424), JSON audit log
- **All frontend libraries bundled locally** — no CDN/internet required

## Business Value: 5 Ways SerWebs Saves Money

### 1. Eliminates travel to remote sites for console access

**Problem:** Network equipment (routers, switches, firewalls) requires physical console access for initial setup, firmware recovery, and out-of-band troubleshooting. Engineers travel to data centers, remote offices, or cell towers — each trip costs fuel, hours of labor, and downtime while the engineer is in transit.

**How SerWebs helps:** A $10 Raspberry Pi with a USB-UART adapter, running SerWebs at the remote site, gives the entire team browser-based console access 24/7. The aggregator mode provides a single dashboard for dozens of sites. One deployment replaces repeated on-site visits.

**Typical savings:** $200-500 per avoided site visit (travel + labor), with some teams making 2-5 console-related trips per month.

### 2. Reduces mean time to repair (MTTR) for network outages

**Problem:** When a router or switch loses its configuration or becomes unreachable over the network, the only recovery path is serial console. Until an engineer physically reaches the device, the outage continues — costing revenue per minute of downtime.

**How SerWebs helps:** Console access is instant — any authorized team member can connect from a browser within seconds. Shared sessions allow senior engineers to observe and guide junior staff in real-time. Session replay (64 KB ring buffer) shows what happened before you connected.

**Typical impact:** Reducing MTTR from hours (travel time) to minutes (browser login) on out-of-band recovery scenarios.

### 3. Creates audit trail for compliance and incident forensics

**Problem:** Regulatory frameworks (PCI DSS, SOX, ISO 27001, HIPAA) require logging of all access to infrastructure components. Serial console sessions are traditionally unmonitored — there is no record of who connected, when, or what commands were executed. Audit failures lead to fines and remediation costs.

**How SerWebs helps:** Every action is logged: login, port open/close, commands sent (audit log + per-port session logs). Terminal recordings in asciicast format provide full playback of sessions. Role-based access (admin/user/viewer) enforces least-privilege. OIDC/SSO integration ties console access to the corporate identity provider.

**Typical savings:** Avoiding audit findings that cost $10K-100K+ in remediation, and having ready evidence during incident investigations.

### 4. Enables automation of serial device provisioning

**Problem:** Configuring dozens of network devices (new deployments, firmware upgrades, factory resets) requires an engineer to sit in front of each console, paste configuration blocks, and verify output. This is repetitive, error-prone, and expensive when done manually.

**How SerWebs helps:** The REST API (`/write`, `/write-wait`) enables scripts and CI/CD pipelines to send commands to serial ports and read responses programmatically. A deployment script can configure 20 switches overnight without human intervention. Profiles auto-apply correct serial settings per device type.

**Typical savings:** Reducing device provisioning from 30-60 minutes of manual work per device to a fully automated pipeline. At scale (50+ devices), this saves weeks of engineering time per deployment cycle.

### 5. Consolidates tooling and reduces license costs

**Problem:** Enterprise console server solutions (Opengear, Lantronix, Raritan) cost $1,000-5,000+ per unit, plus annual maintenance contracts. Terminal server software licenses add recurring costs. Each vendor brings its own management interface, credentials, and update cycle.

**How SerWebs helps:** Open-source, runs on any Linux machine (including $35 SBCs), no per-port licensing fees. One UI and API for all sites via the aggregator. Standard auth (OIDC/SSO) integrates with existing identity infrastructure. Docker deployment makes updates trivial.

**Typical savings:** $2K-10K per site in hardware/license costs for organizations managing 5+ remote locations.

## TODO

### High Priority
- [x] Aggregator: WebSocket connectivity to remote terminal sessions (via ws-url API)
- [x] Aggregator: remote port operations via proxy API
- [x] Aggregator: full CRUD management of remote ports via central UI
- [x] Webhook/email alerting (port connect/disconnect events)
- [x] Syslog forwarding (RFC 3164/5424)
- [x] LDAP / RADIUS / TACACS+ authentication backends
- [ ] Multi-language UI support (i18n)

### Medium Priority
- [x] Telnet gateway (in addition to SSH)
- [x] Recording playback in browser (embedded xterm.js player)
- [x] User management API (add/remove users without editing config.toml)
- [x] Port locking — exclusive access mode
- [ ] Port auto-open on detection (based on profiles with `auto_open: true`)
- [ ] Session log search/grep in UI

### Low Priority
- [ ] Dark/light theme toggle
- [ ] Custom terminal fonts
- [ ] SNMP trap integration for device monitoring
- [ ] Prometheus metrics exporter (`/metrics` in Prometheus format)
- [ ] Mobile-optimized layout improvements
- [ ] Plugin system for custom serial protocols
