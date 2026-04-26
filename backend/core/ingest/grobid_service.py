"""Backend-managed access to a local GROBID service."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

DEFAULT_GROBID_URL = "http://localhost:8070"
DEFAULT_GROBID_DOCKER_IMAGE = "grobid/grobid:0.9.0-crf"
DEFAULT_GROBID_CONTAINER_NAME = "pdfreader-grobid"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 90
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class GrobidServiceConfig:
    """Configuration for locating or starting the GROBID service."""

    url: str = DEFAULT_GROBID_URL
    auto_start: bool = False
    docker_image: str = DEFAULT_GROBID_DOCKER_IMAGE
    container_name: str = DEFAULT_GROBID_CONTAINER_NAME
    docker_port: int = 8070
    startup_timeout_seconds: int = DEFAULT_STARTUP_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "GrobidServiceConfig":
        """Build service settings from environment variables."""

        url = os.environ.get("GROBID_URL", DEFAULT_GROBID_URL).rstrip("/")
        default_port = urlparse(url).port or 8070
        return cls(
            url=url,
            auto_start=_is_enabled(os.environ.get("GROBID_AUTO_START")),
            docker_image=os.environ.get("GROBID_DOCKER_IMAGE", DEFAULT_GROBID_DOCKER_IMAGE),
            container_name=os.environ.get("GROBID_CONTAINER_NAME", DEFAULT_GROBID_CONTAINER_NAME),
            docker_port=_env_int("GROBID_DOCKER_PORT", default_port),
            startup_timeout_seconds=_env_int("GROBID_STARTUP_TIMEOUT_SECONDS", DEFAULT_STARTUP_TIMEOUT_SECONDS),
        )


def ensure_grobid_service(config: GrobidServiceConfig | None = None) -> str:
    """Return a ready GROBID URL, optionally starting a local Docker service."""

    service_config = config or GrobidServiceConfig.from_env()
    if _is_grobid_ready(service_config.url):
        return service_config.url

    if not service_config.auto_start:
        raise ValueError(
            "GROBID is not running at "
            f"{service_config.url}. Start GROBID, set GROBID_URL, or enable "
            "GROBID_AUTO_START=1 so the backend can start a Docker container."
        )

    if not _is_local_url(service_config.url):
        raise ValueError("GROBID_AUTO_START only supports localhost GROBID_URL values.")

    _start_grobid_container(service_config)
    if _wait_for_grobid(service_config.url, service_config.startup_timeout_seconds):
        return service_config.url

    raise ValueError(
        "The backend started GROBID, but it was not ready at "
        f"{service_config.url} within {service_config.startup_timeout_seconds} seconds."
    )


def _is_grobid_ready(url: str) -> bool:
    """Check whether GROBID is ready to process requests."""

    with httpx.Client(timeout=3.0) as client:
        for path in ("/api/health", "/api/isalive"):
            try:
                response = client.get(f"{url.rstrip('/')}{path}")
            except httpx.HTTPError:
                continue

            if response.status_code == 200:
                return True
    return False


def _start_grobid_container(config: GrobidServiceConfig) -> None:
    """Start or reuse the configured local GROBID Docker container."""

    command = [
        "docker",
        "run",
        "-d",
        "--init",
        "--ulimit",
        "core=0",
        "--name",
        config.container_name,
        "-p",
        f"{config.docker_port}:8070",
        config.docker_image,
    ]
    result = _run_docker(command)
    if result.returncode == 0:
        return

    output = f"{result.stdout}\n{result.stderr}"
    if "Conflict" in output or "already in use" in output:
        start_result = _run_docker(["docker", "start", config.container_name])
        if start_result.returncode == 0:
            return
        output = f"{start_result.stdout}\n{start_result.stderr}"

    raise ValueError(f"Unable to start GROBID Docker container: {output.strip()}")


def _run_docker(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a Docker command and capture output for API error messages."""

    try:
        return subprocess.run(command, capture_output=True, check=False, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Unable to run Docker for GROBID: {error}") from error


def _wait_for_grobid(url: str, timeout_seconds: int) -> bool:
    """Poll GROBID readiness until timeout."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _is_grobid_ready(url):
            return True
        time.sleep(2)
    return False


def _is_enabled(value: str | None) -> bool:
    return bool(value and value.lower() in TRUE_VALUES)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _is_local_url(url: str) -> bool:
    hostname = urlparse(url).hostname
    return hostname in {"localhost", "127.0.0.1", "::1"}
