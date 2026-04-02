FROM python:3.12-slim AS base

# System dependencies for pyudev and LDAP
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libudev-dev \
        libldap2-dev \
        libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install all Python dependencies (cached layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.104.0" \
    "uvicorn[standard]>=0.24.0" \
    "pyserial>=3.5" \
    "pyserial-asyncio>=0.6" \
    "pyudev>=0.24.0" \
    "python-jose[cryptography]>=3.3.0" \
    "bcrypt>=4.0.0" \
    "pydantic>=2.0" \
    "pyyaml>=6.0" \
    "httpx>=0.25.0" \
    "asyncssh>=2.14.0" \
    "ldap3>=2.9" \
    "pyrad>=2.4" \
    "tacacs_plus>=2.6"

# Copy application code
COPY serwebs/ ./serwebs/
COPY frontend/ ./frontend/
COPY config.toml ./

RUN pip install --no-cache-dir --no-deps -e .

# Data directory for persistent storage (mount as volume)
RUN mkdir -p /app/data
VOLUME /app/data

EXPOSE 8080
EXPOSE 2222
EXPOSE 2323

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "-m", "serwebs"]
