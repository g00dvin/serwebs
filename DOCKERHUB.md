# SerWebs — Web Serial Terminal Manager

Lightweight web application for remote management of serial ports (USB-UART adapters). Provides real-time browser terminal, SSH/Telnet gateways, REST API, enterprise auth (LDAP/RADIUS/TACACS+), alerting, syslog, and multi-site aggregation.

## Quick Start

```bash
docker run -d \
  --name serwebs \
  -p 8080:8080 \
  -p 2222:2222 \
  -p 2323:2323 \
  --device /dev/ttyUSB0:/dev/ttyUSB0 \
  --group-add dialout \
  -v serwebs-data:/app/data \
  serwebs
```

Open browser: **http://localhost:8080**
Default credentials: `admin` / `admin`

## Docker Compose

```yaml
services:
  serwebs:
    image: ${DOCKERHUB_USERNAME}/serwebs:latest
    container_name: serwebs
    restart: unless-stopped
    ports:
      - "8080:8080"   # Web UI + REST API
      - "2222:2222"   # SSH gateway (optional)
      - "2323:2323"   # Telnet gateway (optional)
    volumes:
      - ./config.toml:/app/config.toml:ro
      - serwebs-data:/app/data
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
    group_add:
      - dialout
    environment:
      - SERWEBS_CONFIG=/app/config.toml

volumes:
  serwebs-data:
```

## Exposed Ports

| Port | Protocol | Description |
|------|----------|-------------|
| `8080` | HTTP | Web UI, REST API, WebSocket |
| `2222` | SSH | SSH gateway (requires `[ssh] enabled = true`) |
| `2323` | Telnet | Telnet gateway (requires `[telnet] enabled = true`) |

## Volumes

| Path | Description |
|------|-------------|
| `/app/data` | Persistent storage: audit logs, session logs, recordings, user database, port aliases/tags/profiles, port locks |
| `/app/config.toml` | Configuration file (bind-mount read-only) |
| `/app/backends.yaml` | Aggregator backends list (optional, bind-mount) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SERWEBS_CONFIG` | `/app/config.toml` | Path to configuration file |

## Configuration

All settings are in `config.toml`. Minimal example:

```toml
[server]
host = "0.0.0.0"
port = 8080

[auth]
secret_key = "change-me-to-a-random-string"

[[auth.users]]
username = "admin"
password_hash = "$2b$12$LJ3m4ys3ez1jb4mOISCDNONGMKbL9Rq4.hCZdPOsVMnEBkIFHHvG."  # "admin"
role = "admin"

[[auth.users]]
username = "viewer"
password_hash = "$2b$12$LJ3m4ys3ez1jb4mOISCDNONGMKbL9Rq4.hCZdPOsVMnEBkIFHHvG."
role = "viewer"

[serial]
port_patterns = ["/dev/ttyUSB*", "/dev/ttyACM*"]

[ssh]
enabled = true
port = 2222

[telnet]
enabled = false
port = 2323
```

Generate password hash:

```bash
docker run --rm serwebs python -c "import bcrypt; print(bcrypt.hashpw(b'mypassword', bcrypt.gensalt()).decode())"
```

## Configuration Sections

### Authentication

| Section | Description | Required Package |
|---------|-------------|-----------------|
| `[[auth.users]]` | Local users (username + bcrypt hash) | Built-in |
| `[auth.oidc]` | OpenID Connect / SSO | Built-in |
| `[auth.ldap]` | LDAP / Active Directory | `ldap3` (included) |
| `[auth.radius]` | RADIUS | `pyrad` (included) |
| `[auth.tacacs]` | TACACS+ | `tacacs_plus` (included) |

All authentication backends can be enabled simultaneously. Priority: local users → API users → LDAP → RADIUS → TACACS+ → OIDC.

### LDAP Example

```toml
[auth.ldap]
enabled = true
url = "ldap://ldap.example.com:389"
bind_dn = "cn=serwebs,ou=services,dc=example,dc=com"
bind_password = "service-password"
user_base_dn = "ou=users,dc=example,dc=com"
user_filter = "(uid={username})"
admin_groups = ["serwebs-admin"]
default_role = "user"
```

### RADIUS Example

```toml
[auth.radius]
enabled = true
server = "radius.example.com"
secret = "shared-secret"
admin_filter_id = "serwebs-admin"
```

### TACACS+ Example

```toml
[auth.tacacs]
enabled = true
server = "tacacs.example.com"
secret = "shared-secret"
admin_priv_lvl = 15
```

### Alerting

```toml
[alerting]
enabled = true
webhook_url = "https://hooks.slack.com/services/T.../B.../xxx"
smtp_host = "smtp.gmail.com"
smtp_port = 587
smtp_use_tls = true
smtp_username = "alerts@example.com"
smtp_password = "app-password"
smtp_to = ["ops@example.com"]
events = ["port_open", "port_close", "device_lost", "login_failed"]
```

### Syslog

```toml
[syslog]
enabled = true
host = "syslog.example.com"
port = 514
protocol = "udp"
facility = "local0"
format = "rfc5424"
```

### Aggregator (Multi-Site)

```toml
[aggregator]
enabled = true
backends_file = "backends.yaml"
```

`backends.yaml`:

```yaml
backends:
  - name: lab-rack-1
    url: http://192.168.1.10:8080
    token: "eyJ..."
  - name: lab-rack-2
    url: http://192.168.1.11:8080
    username: admin
    password: secret
```

## Deployment Modes

### Single node with serial devices

```bash
docker run -d --name serwebs \
  -p 8080:8080 \
  --device /dev/ttyUSB0 --device /dev/ttyUSB1 \
  --group-add dialout \
  -v ./config.toml:/app/config.toml:ro \
  -v serwebs-data:/app/data \
  serwebs
```

### All serial devices (privileged)

```bash
docker run -d --name serwebs \
  --privileged \
  -p 8080:8080 -p 2222:2222 -p 2323:2323 \
  -v /dev:/dev \
  -v ./config.toml:/app/config.toml:ro \
  -v serwebs-data:/app/data \
  serwebs
```

### Aggregator-only (no local devices)

```bash
docker run -d --name serwebs-aggregator \
  -p 8088:8080 \
  -v ./config.toml:/app/config.toml:ro \
  -v ./backends.yaml:/app/backends.yaml:ro \
  -v serwebs-data:/app/data \
  serwebs
```

## REST API

```bash
# Get auth token
TOKEN=$(curl -s -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# List ports
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/ports

# Open a port
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"settings":{"baudrate":115200}}' \
  http://localhost:8080/api/ports/ttyUSB0/open

# Write to port
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data":"show version\r\n"}' \
  http://localhost:8080/api/ports/ttyUSB0/write

# Health check (no auth)
curl http://localhost:8080/health
```

## Key Features

- Real-time serial terminal (xterm.js) with shared sessions
- SSH and Telnet gateways for CLI access
- REST API for automation (`/write`, `/write-wait`)
- Terminal recording with in-browser playback
- Enterprise auth: LDAP, RADIUS, TACACS+, OIDC/SSO
- Webhook + email alerting on port events
- Syslog forwarding (RFC 3164/5424) for SIEM
- User management API (no config file editing)
- Port locking (exclusive access mode)
- Multi-site aggregator with full CRUD
- Role-based access: admin, user, viewer
- USB hot-plug detection via udev
- Audit log with rotation (JSON Lines)
- Port tags, aliases, device profiles
- Hex mode, timestamps, macros

## Tags

- `latest` — latest stable release
- `X.Y.Z` — specific version (e.g., `0.2.0`)
- `X.Y` — latest patch for minor version (e.g., `0.2`)
- `X` — latest minor for major version (e.g., `0`)

## Source Code

See project repository for full documentation, configuration reference, and comparison with enterprise console servers (Opengear, Lantronix).
