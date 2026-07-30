from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import snowflake.connector
from dotenv import dotenv_values
from pymongo import MongoClient

from app.logging_config import (
    JsonFormatter,
    ServiceFilter,
    configure_logging,
    sanitized_exception_info,
)
from scripts.load_population import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_SOURCE_MANIFEST_PATH,
    refresh_population,
)
from scripts.world_bank_indicators import DEFAULT_CSV_PATH as WDI_CSV_PATH
from scripts.world_bank_indicators import DEFAULT_MANIFEST_PATH as WDI_MANIFEST_PATH
from scripts.world_bank_indicators import publish_snapshot as publish_wdi_snapshot
from scripts.world_bank_indicators import rollback_snapshot as rollback_wdi_snapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_PATH = REPOSITORY_ROOT / ".env.example"
ENV_PATH = REPOSITORY_ROOT / ".env"
STATE_PATH = REPOSITORY_ROOT / ".setup-state.json"
SETUP_OUTPUT_ROOT = REPOSITORY_ROOT / "outputs" / "setup"
POPULATION_MANIFEST_PATH = REPOSITORY_ROOT / DEFAULT_MANIFEST_PATH
POPULATION_SNAPSHOT_PATH = (
    REPOSITORY_ROOT / "data" / "external" / "world_bank_population_2020.csv"
)
POPULATION_SOURCE_MANIFEST_PATH = REPOSITORY_ROOT / DEFAULT_SOURCE_MANIFEST_PATH
WDI_SNAPSHOT_PATH = REPOSITORY_ROOT / WDI_CSV_PATH
WDI_SNAPSHOT_MANIFEST_PATH = REPOSITORY_ROOT / WDI_MANIFEST_PATH
COMPOSE_PROJECT_NAME = "covid-platform"
REQUIRED_PORTS = (8000, 8050, 27017)
PORT_SERVICES = {
    8000: "FastAPI",
    8050: "Dash dashboard",
    27017: "MongoDB",
}
SETUP_STEP_COUNT = 9
REQUIRED_IGNORE_PATTERNS = (
    ".env",
    ".env.backup-*",
    ".setup-state.json",
    "outputs/setup/",
)
REQUIRED_ENVIRONMENT = (
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_BOOTSTRAP_ROLE",
    "SNOWFLAKE_ROLE",
    "SNOWFLAKE_API_ROLE",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_SCHEMA",
    "SNOWFLAKE_API_SCHEMA",
    "MONGO_ROOT_USERNAME",
    "MONGO_ROOT_PASSWORD",
    "MONGO_DATABASE",
    "MONGODB_URI",
    "REDIS_URL",
)
SQL_FILES = {
    "account_setup": REPOSITORY_ROOT / "sql" / "00_project_setup.sql",
    "project_objects": REPOSITORY_ROOT / "sql" / "00_project_objects.sql",
    "mapping": REPOSITORY_ROOT / "sql" / "02_create_country_mapping.sql",
    "staging": REPOSITORY_ROOT / "sql" / "03_create_staging_view.sql",
    "population_verify": REPOSITORY_ROOT / "sql" / "04_verify_population_data.sql",
    "world_bank_context": REPOSITORY_ROOT / "sql" / "04_create_world_bank_context.sql",
    "mart": REPOSITORY_ROOT / "sql" / "05_create_enriched_view.sql",
    "reporting": REPOSITORY_ROOT / "sql" / "06_create_reporting_objects.sql",
    "exploration": REPOSITORY_ROOT / "sql" / "01_data_exploration.sql",
    "analysis": REPOSITORY_ROOT / "sql" / "07_analysis_queries.sql",
}
WORLD_BANK_PUBLICATION_INPUTS = (
    REPOSITORY_ROOT / "scripts" / "load_population.py",
    POPULATION_SNAPSHOT_PATH,
    POPULATION_SOURCE_MANIFEST_PATH,
    REPOSITORY_ROOT / "scripts" / "world_bank_indicators.py",
    WDI_SNAPSHOT_PATH,
    WDI_SNAPSHOT_MANIFEST_PATH,
)
ANALYTICAL_MART_INPUTS = (
    SQL_FILES["population_verify"],
    SQL_FILES["world_bank_context"],
    SQL_FILES["mart"],
    SQL_FILES["reporting"],
)
logger = logging.getLogger(__name__)


class BootstrapError(RuntimeError):
    """A sanitized, user-actionable bootstrap failure."""

    def __init__(
        self,
        message: str,
        *,
        likely_cause: str = "A required prerequisite or postcondition was not met.",
        fixes: tuple[str, ...] = (),
        retry: str | None = None,
        technical_reference: str | None = None,
    ) -> None:
        super().__init__(message)
        self.likely_cause = likely_cause
        self.fixes = fixes
        self.retry = retry
        self.technical_reference = technical_reference


class BootstrapAuditFilter(logging.Filter):
    """Keep third-party connector records out of the persistent audit file."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in {
            "__main__",
            "scripts.bootstrap",
            "scripts.load_population",
            "scripts.world_bank_indicators",
        }


def console(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(f"{message}\n")
    stream.flush()


def _setup_command(*, resume: bool = False) -> str:
    configured = os.getenv("SETUP_RESUME_COMMAND" if resume else "SETUP_LAUNCH_COMMAND")
    if configured:
        return configured
    if os.name == "nt":
        return ".\\setup.ps1 --resume" if resume else ".\\setup.ps1"
    return "./setup.sh --resume" if resume else "./setup.sh"


def _start_command() -> str:
    if configured := os.getenv("SETUP_START_COMMAND"):
        return configured
    return ".\\start.ps1" if os.name == "nt" else "./start.sh"


def _stop_command() -> str:
    if configured := os.getenv("SETUP_STOP_COMMAND"):
        return configured
    return ".\\stop.ps1" if os.name == "nt" else "./stop.sh"


def _setup_progress(
    number: int,
    title: str,
    explanation: str,
    *,
    total: int = SETUP_STEP_COUNT,
) -> None:
    console(f"\nStep {number} of {total} -- {title}")
    console(explanation)


def _flush_log_handlers() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception as exc:
            logger.exception(
                "audit_log_flush_failed",
                exc_info=sanitized_exception_info(exc),
            )


def _show_failure(
    exc: BootstrapError,
    *,
    audit_path: Path,
    default_retry: str,
) -> None:
    console("\nSETUP COULD NOT CONTINUE", error=True)
    console("=" * 26, error=True)
    console(f"What happened: {exc}", error=True)
    console(f"Likely cause: {exc.likely_cause}", error=True)
    console("How to fix:", error=True)
    fixes = exc.fixes or (
        "Review the nearby README troubleshooting entry and correct the reported prerequisite.",
    )
    for fix in fixes:
        console(f"  - {fix}", error=True)
    console(f"Then run: {exc.retry or default_retry}", error=True)
    reference = exc.technical_reference or str(audit_path)
    console(f"Technical reference: {reference}", error=True)
    if reference != str(audit_path):
        console(f"Setup audit log: {audit_path}", error=True)


def _show_setup_success(audit_path: Path) -> None:
    console("\nSETUP COMPLETED SUCCESSFULLY")
    console("=" * 28)
    console("The Snowflake objects, local services, and application checks are ready.")
    _show_urls()
    console(f"Start later with: {_start_command()}")
    console(f"Stop safely with: {_stop_command()}")
    console(f"Setup audit log: {audit_path}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _atomic_write(path: Path, content: str, *, owner_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        staging.write_text(content, encoding="utf-8")
        if owner_only and os.name == "posix":
            staging.chmod(0o600)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _repository_path_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPOSITORY_ROOT.resolve()))
    except ValueError:
        return str(path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        path_label = _repository_path_label(path)
        raise BootstrapError(
            f"Required setup input is missing: {path_label}",
            likely_cause="The checkout is incomplete, or setup is running from a branch that does not contain the required input.",
            fixes=(
                f"Restore {path_label} from Git and rerun setup.",
                "Confirm that the Codespace is on the expected project branch and commit.",
            ),
            technical_reference=path_label,
        ) from exc
    return digest.hexdigest()


def _input_checksum(*paths: Path, values: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(_repository_path_label(path).encode("utf-8"))
        digest.update(bytes.fromhex(_file_sha256(path)))
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _world_bank_publication_checksum() -> str:
    return _input_checksum(
        *WORLD_BANK_PUBLICATION_INPUTS,
        values=("2020", "wdi-source-2-2019-2021"),
    )


def _analytical_marts_checksum(publication_checksum: str) -> str:
    # Snowflake can outlive a Codespace. Use committed publication inputs here so
    # resume state never depends on an ignored receipt from a previous workspace.
    return _input_checksum(
        *ANALYTICAL_MART_INPUTS,
        values=(publication_checksum,),
    )


def configure_audit_logging(command: str) -> Path:
    configure_logging("project-bootstrap", os.getenv("LOG_LEVEL", "INFO"))
    root_logger = logging.getLogger()
    for existing_handler in root_logger.handlers:
        if not isinstance(existing_handler, logging.FileHandler):
            existing_handler.setLevel(logging.CRITICAL + 1)
    for external_logger in ("snowflake.connector", "urllib3", "pymongo"):
        logging.getLogger(external_logger).setLevel(logging.CRITICAL)
    SETUP_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = SETUP_OUTPUT_ROOT / f"{command}-{timestamp}-{secrets.token_hex(3)}.jsonl"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ServiceFilter("project-bootstrap"))
    handler.addFilter(BootstrapAuditFilter())
    root_logger.addHandler(handler)
    logger.info("bootstrap_audit_started", extra={"command": command})
    return path


def _run_command(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    logger.info("local_command_started", extra={"executable": command[0]})
    command_environment = os.environ.copy()
    command_environment["COMPOSE_PROJECT_NAME"] = COMPOSE_PROJECT_NAME
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=command_environment,
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if check and result.returncode != 0:
        raise BootstrapError(
            f"Command failed: {command[0]}. See the setup audit log for context."
        )
    logger.info(
        "local_command_completed",
        extra={"executable": command[0], "return_code": result.returncode},
    )
    return result


def _is_placeholder(key: str, value: str | None) -> bool:
    if value is None or not value.strip():
        return True
    lowered = value.lower()
    return (
        lowered.startswith("your_")
        or lowered.startswith("replace_with")
        or lowered in {"changeme", "password"}
        or (key.endswith("PASSWORD") and "password" in lowered)
    )


def _missing_environment(values: dict[str, str | None]) -> list[str]:
    return [
        key for key in REQUIRED_ENVIRONMENT if _is_placeholder(key, values.get(key))
    ]


def _example_keys() -> list[str]:
    keys = []
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            keys.append(stripped.split("=", maxsplit=1)[0].strip())
    return keys


def _dotenv_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
    return f"'{escaped}'"


def _serialize_environment(values: dict[str, str]) -> str:
    return (
        "\n".join(
            f"{key}={_dotenv_quote(values.get(key, ''))}" if key else ""
            for key in _example_keys()
        )
        + "\n"
    )


def _generated_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def configure_environment(*, non_interactive: bool) -> dict[str, str]:
    example = {
        key: str(value or "") for key, value in dotenv_values(ENV_EXAMPLE_PATH).items()
    }
    current = {key: str(value or "") for key, value in dotenv_values(ENV_PATH).items()}
    values = {**example, **current}
    if non_interactive:
        missing = _missing_environment(values)
        if missing:
            raise BootstrapError(
                "Non-interactive setup requires a complete .env. Missing: "
                + ", ".join(missing)
            )
        return values

    if _is_placeholder("SNOWFLAKE_ACCOUNT", values.get("SNOWFLAKE_ACCOUNT")):
        console(
            "Snowflake account identifier\n"
            "  Find this in Snowsight under account details. Enter the connector form\n"
            "  organization-account, for example acme-xy12345. Do not enter a URL,\n"
            "  https:// prefix, region hostname, or password."
        )
        entered = input("SNOWFLAKE_ACCOUNT: ").strip()
        if not entered:
            raise BootstrapError(
                "SNOWFLAKE_ACCOUNT is required.",
                likely_cause="The Snowflake account identifier was left blank.",
                fixes=(
                    "Copy the organization-account identifier from Snowflake account details.",
                    "Do not use the Snowsight browser URL or a hostname ending in snowflakecomputing.com.",
                ),
            )
        values["SNOWFLAKE_ACCOUNT"] = entered
    if _is_placeholder("SNOWFLAKE_USER", values.get("SNOWFLAKE_USER")):
        console(
            "Snowflake username\n"
            "  Enter the login name created with your Snowflake account, for example\n"
            "  BOOTCAMP_USER. This is not your organization name or email unless you\n"
            "  deliberately chose your email as the username."
        )
        entered = input("SNOWFLAKE_USER: ").strip()
        if not entered:
            raise BootstrapError(
                "SNOWFLAKE_USER is required.",
                likely_cause="The Snowflake login name was left blank.",
            )
        values["SNOWFLAKE_USER"] = entered
    if _is_placeholder("SNOWFLAKE_PASSWORD", values.get("SNOWFLAKE_PASSWORD")):
        console(
            "Snowflake password\n"
            "  Enter the password for the Snowflake username above. Input is hidden,\n"
            "  is stored only in the ignored local .env file, and is never written to\n"
            "  the structured setup audit log."
        )
        password = getpass.getpass("SNOWFLAKE_PASSWORD (hidden): ")
        if not password:
            raise BootstrapError(
                "SNOWFLAKE_PASSWORD is required.",
                likely_cause="The hidden Snowflake password prompt was left blank.",
            )
        values["SNOWFLAKE_PASSWORD"] = password
    if _is_placeholder("MONGO_ROOT_PASSWORD", values.get("MONGO_ROOT_PASSWORD")):
        console(
            "Local MongoDB root password\n"
            "  This protects the project-owned local MongoDB container; it is unrelated\n"
            "  to Snowflake. Leave the prompt blank to generate a strong value. The\n"
            "  value is stored only in the ignored local .env file and is not logged."
        )
        mongo_password = getpass.getpass(
            "MONGO_ROOT_PASSWORD (hidden; blank generates one): "
        )
        values["MONGO_ROOT_PASSWORD"] = mongo_password or _generated_password()
    values.setdefault("SNOWFLAKE_BOOTSTRAP_ROLE", "ACCOUNTADMIN")
    values["MONGODB_URI"] = (
        f"mongodb://{quote_plus(values['MONGO_ROOT_USERNAME'])}:"
        f"{quote_plus(values['MONGO_ROOT_PASSWORD'])}"
        f"@localhost:27017/{values['MONGO_DATABASE']}?authSource=admin"
    )
    missing = _missing_environment(values)
    if missing:
        raise BootstrapError("Environment remains incomplete: " + ", ".join(missing))
    if ENV_PATH.exists():
        backup = REPOSITORY_ROOT / (
            f".env.backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{secrets.token_hex(3)}"
        )
        shutil.copy2(ENV_PATH, backup)
        if os.name == "posix":
            backup.chmod(0o600)
    serialized = _serialize_environment(values)
    _atomic_write(ENV_PATH, serialized, owner_only=True)
    verified = {key: str(value or "") for key, value in dotenv_values(ENV_PATH).items()}
    if any(verified.get(key) != values.get(key, "") for key in _example_keys()):
        raise BootstrapError("The generated .env failed dotenv round-trip validation.")
    logger.info("environment_configuration_written")
    return verified


def load_configured_environment() -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(ENV_PATH).items()}
    missing = _missing_environment(values)
    if missing:
        raise BootstrapError(
            "Configured environment is incomplete: " + ", ".join(missing)
        )
    return values


def load_runtime_environment() -> dict[str, str]:
    """Prefer container runtime values while retaining the persisted setup file."""
    values = load_configured_environment()
    for key in REQUIRED_ENVIRONMENT:
        runtime_value = os.getenv(key)
        if runtime_value:
            values[key] = runtime_value
    return values


class SetupState:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        if path.exists():
            self.payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.payload = {
                "schema_version": 1,
                "context": {},
                "completed_steps": {},
            }

    def set_context(self, context: dict[str, str]) -> None:
        if self.payload.get("context") not in ({}, context):
            self.payload["completed_steps"] = {}
        self.payload["context"] = context
        self._save()

    def can_resume(
        self,
        step: str,
        checksum: str,
        context: dict[str, str],
        postcondition: Callable[[], bool],
    ) -> bool:
        record = self.payload.get("completed_steps", {}).get(step)
        if not record or self.payload.get("context") != context:
            return False
        if record.get("input_checksum") != checksum:
            return False
        try:
            return bool(postcondition())
        except Exception:
            return False

    def mark_complete(self, step: str, checksum: str) -> None:
        self.payload.setdefault("completed_steps", {})[step] = {
            "completed_at": _utc_now(),
            "input_checksum": checksum,
            "verification_status": "passed",
        }
        self._save()

    def _save(self) -> None:
        _atomic_write(self.path, _json_text(self.payload), owner_only=True)


def _verify_gitignore_contract() -> None:
    patterns = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = [
        pattern for pattern in REQUIRED_IGNORE_PATTERNS if pattern not in patterns
    ]
    if missing:
        raise BootstrapError(
            ".gitignore is missing setup security patterns: " + ", ".join(missing)
        )
    docker_patterns = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required_docker_patterns = {".env", ".env.backup-*", ".setup-state.json"}
    missing_docker_patterns = sorted(required_docker_patterns - docker_patterns)
    if missing_docker_patterns:
        raise BootstrapError(
            ".dockerignore is missing setup security patterns: "
            + ", ".join(missing_docker_patterns)
        )


def _compose_port_owners() -> set[int]:
    result = _run_command(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={COMPOSE_PROJECT_NAME}",
            "--format",
            "{{json .}}",
        ],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return set()
    ports: set[int] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for match in re.findall(
            r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):(\d+)->", record.get("Ports", "")
        ):
            ports.add(int(match))
    return ports


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def doctor_local(*, offer_restart: bool = False, non_interactive: bool = False) -> None:
    if shutil.which("uv") is None:
        raise BootstrapError("uv is not installed or is not available on PATH.")
    if shutil.which("docker") is None:
        raise BootstrapError(
            "Docker CLI is not installed or is not available on PATH.",
            likely_cause="Docker Desktop or Docker Engine is not installed, or the terminal has not picked up its PATH change.",
            fixes=(
                "Install Docker Desktop on Windows/macOS, or Docker Engine plus the Compose plugin on Linux.",
                "Close and reopen the terminal after installation, then run docker --version.",
            ),
        )
    docker_info = _run_command(["docker", "info"], check=False)
    if docker_info.returncode != 0:
        raise BootstrapError(
            "Docker is installed, but its engine is not responding.",
            likely_cause="Docker Desktop is still starting, or the Docker daemon/service is stopped.",
            fixes=(
                "Start Docker Desktop and wait until it reports that the engine is running.",
                "On Linux, start the Docker service and ensure your user may access the Docker socket.",
                "Confirm both client and server sections appear when you run docker info.",
            ),
            retry="docker info, then " + _setup_command(resume=True),
        )
    compose_version = _run_command(["docker", "compose", "version"], check=False)
    if compose_version.returncode != 0:
        raise BootstrapError(
            "The Docker Compose plugin is not available.",
            likely_cause="Docker was installed without the Compose v2 plugin.",
            fixes=(
                "Install or enable Docker Compose v2.",
                "Confirm docker compose version succeeds (with a space, not docker-compose).",
            ),
        )
    _verify_gitignore_contract()
    with tempfile.NamedTemporaryFile(dir=REPOSITORY_ROOT, delete=True):
        pass
    occupied = {port for port in REQUIRED_PORTS if _port_is_open(port)}
    project_ports = _compose_port_owners() if occupied else set()
    unrelated = occupied - project_ports
    if unrelated:
        details = ", ".join(
            f"{port} ({PORT_SERVICES[port]})" for port in sorted(unrelated)
        )
        raise BootstrapError(
            "Required ports are used by another process: " + details,
            likely_cause="Another application, or containers from a different Compose project, already own a required host port.",
            fixes=(
                "Windows: inspect an owner with Get-NetTCPConnection -LocalPort <port> and Get-Process -Id <OwningProcess>.",
                "macOS/Linux: inspect an owner with lsof -i :<port> or ss -ltnp.",
                "Stop or reconfigure only the process you recognize. Setup will never terminate it automatically.",
            ),
        )
    if occupied and offer_restart and not non_interactive:
        answer = input(
            "Existing covid-platform containers use required ports. Restart them? [y/N]: "
        ).strip()
        if answer.lower() in {"y", "yes"}:
            _run_command(["docker", "compose", "restart"], capture_output=False)
    logger.info(
        "doctor_local_passed", extra={"occupied_project_ports": sorted(occupied)}
    )


def doctor_container_workspace() -> None:
    """Validate host-mounted files without requiring Docker inside the container."""
    _verify_gitignore_contract()
    with tempfile.NamedTemporaryFile(dir=REPOSITORY_ROOT, delete=True):
        pass
    logger.info("doctor_container_workspace_passed")


def connect_snowflake(
    values: dict[str, str],
    *,
    role: str,
    include_project_context: bool = True,
) -> snowflake.connector.SnowflakeConnection:
    logger.info("snowflake_connection_started", extra={"role": role})
    parameters: dict[str, Any] = {
        "account": values["SNOWFLAKE_ACCOUNT"],
        "user": values["SNOWFLAKE_USER"],
        "password": values["SNOWFLAKE_PASSWORD"],
        "role": role,
    }
    if include_project_context:
        parameters.update(
            {
                "warehouse": values.get("SNOWFLAKE_WAREHOUSE"),
                "database": values.get("SNOWFLAKE_DATABASE"),
                "schema": values.get("SNOWFLAKE_SCHEMA"),
            }
        )
    try:
        return snowflake.connector.connect(**parameters)
    except snowflake.connector.Error as exc:
        logger.exception(
            "snowflake_connection_failed",
            extra={"role": role, "error_type": type(exc).__name__},
            exc_info=sanitized_exception_info(exc),
        )
        raise _snowflake_bootstrap_error(exc, role=role) from exc


def _snowflake_bootstrap_error(
    exc: snowflake.connector.Error,
    *,
    role: str,
) -> BootstrapError:
    message = str(exc).lower()
    if any(
        term in message for term in ("incorrect username", "password", "authentication")
    ):
        return BootstrapError(
            "Snowflake rejected the configured username or password.",
            likely_cause="SNOWFLAKE_USER or SNOWFLAKE_PASSWORD does not match the target Snowflake account.",
            fixes=(
                "Sign in to Snowsight with the same username to confirm the password.",
                "Correct SNOWFLAKE_USER or SNOWFLAKE_PASSWORD in .env; do not paste credentials into commands or logs.",
            ),
        )
    if "account" in message and any(
        term in message
        for term in ("invalid", "incorrect", "does not exist", "not found")
    ):
        return BootstrapError(
            "Snowflake could not resolve the configured account identifier.",
            likely_cause="SNOWFLAKE_ACCOUNT is not in connector form organization-account.",
            fixes=(
                "Copy the account identifier from Snowsight account details.",
                "Use a value such as acme-xy12345, not a browser URL, region hostname, or https:// address.",
            ),
        )
    if any(
        term in message for term in ("role", "warehouse", "privilege", "not authorized")
    ):
        return BootstrapError(
            f"Snowflake would not activate the required role or warehouse for {role}.",
            likely_cause="The configured user lacks the role grant, warehouse privilege, or project objects from an earlier setup step.",
            fixes=(
                "For first setup, confirm SNOWFLAKE_BOOTSTRAP_ROLE=ACCOUNTADMIN and rerun setup with --resume.",
                "For application access, confirm COVID_APP_ROLE is granted to the configured user and COVID_WH exists.",
                "Do not replace the application role with ACCOUNTADMIN.",
            ),
        )
    return BootstrapError(
        "Snowflake could not be reached or did not accept the connection.",
        likely_cause="The account identifier, network connection, authentication, or selected role is unavailable.",
        fixes=(
            "Confirm internet access and that you can sign in to the same Snowflake account in Snowsight.",
            "Review SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, and the selected role in .env.",
        ),
    )


def doctor_configured(values: dict[str, str]) -> None:
    connection = connect_snowflake(
        values,
        role=values.get("SNOWFLAKE_BOOTSTRAP_ROLE", "ACCOUNTADMIN"),
        include_project_context=False,
    )
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT CURRENT_USER(), CURRENT_ROLE()")
            session_context = cursor.fetchone()
            if session_context is None:
                raise BootstrapError(
                    "Snowflake authentication returned no session context."
                )
            if str(session_context[1]).upper() != "ACCOUNTADMIN":
                raise BootstrapError(
                    "SNOWFLAKE_BOOTSTRAP_ROLE must resolve to ACCOUNTADMIN."
                )
            cursor.execute("SHOW DATABASES LIKE 'COVID19_EPIDEMIOLOGICAL_DATA'")
            if cursor.fetchone() is None:
                raise BootstrapError(
                    "Snowflake Marketplace database COVID19_EPIDEMIOLOGICAL_DATA is missing.",
                    likely_cause="The free Marketplace listing was not added to this Snowflake account using the required database name.",
                    fixes=(
                        "In Snowsight, add the free COVID-19 Epidemiological Data listing.",
                        "Name the installed database exactly COVID19_EPIDEMIOLOGICAL_DATA.",
                    ),
                )
            try:
                cursor.execute(
                    "SELECT 1 FROM "
                    "COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL LIMIT 1"
                )
                if cursor.fetchone() is None:
                    raise BootstrapError(
                        "The Marketplace ECDC_GLOBAL source returned no accessible rows."
                    )
            except snowflake.connector.Error as exc:
                logger.exception(
                    "marketplace_source_validation_failed",
                    extra={"error_type": type(exc).__name__},
                    exc_info=sanitized_exception_info(exc),
                )
                raise BootstrapError(
                    "The required Marketplace object PUBLIC.ECDC_GLOBAL is not accessible.",
                    likely_cause="The listing is missing, was installed under a different database name, or the bootstrap role cannot use it.",
                    fixes=(
                        "Confirm the database is named COVID19_EPIDEMIOLOGICAL_DATA.",
                        "In Snowsight, verify PUBLIC.ECDC_GLOBAL opens and contains data.",
                    ),
                ) from exc
        finally:
            cursor.close()
    finally:
        connection.close()
    logger.info("doctor_configured_passed")


def execute_sql_file(connection: Any, path: Path) -> None:
    logger.info("sql_file_started", extra={"sql_file": path.name})
    statement_count = 0
    try:
        with path.open("r", encoding="utf-8") as sql_file:
            for cursor in connection.execute_stream(sql_file, remove_comments=True):
                try:
                    statement_count += 1
                    logger.info(
                        "sql_statement_completed",
                        extra={
                            "sql_file": path.name,
                            "statement_number": statement_count,
                            "query_id": getattr(cursor, "sfqid", None),
                        },
                    )
                finally:
                    cursor.close()
    except snowflake.connector.Error as exc:
        failed_statement = statement_count + 1
        logger.exception(
            "sql_file_failed",
            extra={
                "sql_file": path.name,
                "statement_number": failed_statement,
                "error_type": type(exc).__name__,
            },
            exc_info=sanitized_exception_info(exc),
        )
        raise _sql_file_bootstrap_error(path, failed_statement, exc) from exc
    logger.info(
        "sql_file_completed",
        extra={"sql_file": path.name, "statement_count": statement_count},
    )


def _sql_file_bootstrap_error(
    path: Path,
    statement_number: int,
    exc: snowflake.connector.Error,
) -> BootstrapError:
    query_id = getattr(exc, "sfqid", None)
    technical_reference = f"{path.name}: statement {statement_number}"
    if query_id:
        technical_reference = f"{technical_reference}; query ID {query_id}"

    if path.name == SQL_FILES["account_setup"].name:
        return BootstrapError(
            f"Snowflake rejected account setup statement {statement_number} in {path.name}.",
            likely_cause="The bootstrap role lacks ACCOUNTADMIN authority, or the required Marketplace shared database is not installed under the expected name.",
            fixes=(
                "Confirm SNOWFLAKE_BOOTSTRAP_ROLE=ACCOUNTADMIN and that the configured user can activate it.",
                "Confirm the Marketplace database is named COVID19_EPIDEMIOLOGICAL_DATA; shared access is granted with IMPORTED PRIVILEGES.",
                "After correcting the account, rerun setup with --resume.",
            ),
            technical_reference=technical_reference,
        )

    return BootstrapError(
        f"Snowflake rejected statement {statement_number} in {path.name}.",
        likely_cause="The active project role lacks a required grant, or a prerequisite Snowflake object is unavailable.",
        fixes=(
            "Run the account setup phase first and confirm COVID_PROJECT_ADMIN is granted to the deployment user.",
            "Rerun setup with --resume after correcting the reported prerequisite.",
        ),
        technical_reference=technical_reference,
    )


def bootstrap_account_objects_and_roles(connection: Any) -> None:
    """Create account objects, then grant roles before any project-role SQL."""
    execute_sql_file(connection, SQL_FILES["account_setup"])
    cursor = connection.cursor()
    try:
        cursor.execute("USE ROLE ACCOUNTADMIN")
        cursor.execute("SELECT CURRENT_USER()")
        row = cursor.fetchone()
        if row is None or not row[0]:
            raise BootstrapError("Snowflake returned no current deployment user.")
        current_user = row[0]
        cursor.execute(
            "GRANT ROLE COVID_PROJECT_ADMIN TO USER IDENTIFIER(%s)",
            (current_user,),
        )
        cursor.execute(
            "GRANT ROLE COVID_APP_ROLE TO USER IDENTIFIER(%s)",
            (current_user,),
        )
    finally:
        cursor.close()


def _query_one(connection: Any, query: str, parameters: tuple[Any, ...] = ()) -> Any:
    cursor = connection.cursor()
    try:
        cursor.execute(query, parameters)
        return cursor.fetchone()
    finally:
        cursor.close()


def _execute(connection: Any, query: str, parameters: tuple[Any, ...] = ()) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(query, parameters)
    finally:
        cursor.close()


def _object_exists(connection: Any, object_type: str, name: str) -> bool:
    if object_type not in {"ROLES", "WAREHOUSES", "DATABASES"}:
        raise ValueError("Unsupported SHOW object type.")
    cursor = connection.cursor()
    try:
        cursor.execute(f"SHOW {object_type} LIKE '{name}'")
        return cursor.fetchone() is not None
    finally:
        cursor.close()


def _current_user_has_roles(connection: Any, expected_roles: set[str]) -> bool:
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT CURRENT_USER()")
        row = cursor.fetchone()
        if row is None:
            return False
        cursor.execute("SHOW GRANTS TO USER IDENTIFIER(%s)", (row[0],))
        role_column = _result_column_index(cursor, "role")
        granted_roles = {
            str(grant[role_column]).upper()
            for grant in cursor.fetchall()
            if grant[role_column] is not None
        }
        missing_roles = {role.upper() for role in expected_roles} - granted_roles
        if missing_roles:
            logger.warning(
                "snowflake_user_role_postcondition_failed",
                extra={"missing_roles": sorted(missing_roles)},
            )
            return False
        return True
    finally:
        cursor.close()


def _result_column_index(cursor: Any, column_name: str) -> int:
    """Resolve a DB-API result column without relying on a vendor's order."""
    description = cursor.description or ()
    expected_name = column_name.casefold()
    for index, column in enumerate(description):
        if str(column[0]).casefold() == expected_name:
            return index
    raise BootstrapError(
        f"Snowflake returned an unsupported result shape: missing {column_name!r} column.",
        likely_cause="The Snowflake connector or SHOW command output is incompatible with the setup verifier.",
        fixes=(
            "Use the project-pinned setup container and rerun setup with --resume.",
            "If the problem continues, report the named missing column without sharing credentials or raw connector output.",
        ),
    )


def _setup_context(values: dict[str, str]) -> dict[str, str]:
    return {
        "snowflake_account_hash": hashlib.sha256(
            values["SNOWFLAKE_ACCOUNT"].encode("utf-8")
        ).hexdigest(),
        "snowflake_user": values["SNOWFLAKE_USER"],
        "database": values["SNOWFLAKE_DATABASE"],
    }


def _run_step(
    *,
    state: SetupState,
    context: dict[str, str],
    name: str,
    checksum: str,
    action: Callable[[], None],
    postcondition: Callable[[], bool],
    resume: bool,
) -> None:
    if resume and state.can_resume(name, checksum, context, postcondition):
        logger.info("setup_step_resumed", extra={"step": name})
        return
    logger.info("setup_step_started", extra={"step": name})
    action()
    if not postcondition():
        raise BootstrapError(f"Postcondition failed for setup step: {name}")
    state.mark_complete(name, checksum)
    logger.info("setup_step_completed", extra={"step": name})


def _population_manifest() -> dict[str, Any]:
    if not POPULATION_MANIFEST_PATH.is_file():
        raise BootstrapError("Population manifest is missing.")
    return json.loads(POPULATION_MANIFEST_PATH.read_text(encoding="utf-8"))


def _population_matches(connection: Any) -> bool:
    try:
        expected = int(_population_manifest()["loaded_rows"])
        row = _query_one(
            connection,
            "SELECT COUNT(*) FROM COVID_ANALYTICS.RAW.WORLD_BANK_POPULATION_2020",
        )
        return row is not None and int(row[0]) == expected
    except Exception:
        return False


def _frozen_denominator_ready(connection: Any) -> bool:
    """Protect an approved denominator from being coupled to legacy source reloads."""
    try:
        row = _query_one(
            connection,
            """
            SELECT COUNT(*), COUNT_IF(NOT IS_FROZEN)
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR
            """,
        )
        return row is not None and int(row[0]) > 0 and int(row[1]) == 0
    except Exception:
        return False


def _snowflake_objects_ready(connection: Any) -> bool:
    checks = (
        "SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED",
        "SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS",
        "SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_CONTEXT_ANALYSIS",
        "SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR",
    )
    return all(_query_one(connection, query) is not None for query in checks)


def _wdi_snapshot_matches(connection: Any) -> bool:
    try:
        expected = json.loads(WDI_SNAPSHOT_MANIFEST_PATH.read_text(encoding="utf-8"))[
            "snapshot_id"
        ]
        row = _query_one(
            connection,
            """
            SELECT SNAPSHOT_ID
            FROM COVID_ANALYTICS.RAW.WORLD_BANK_INDICATOR_SNAPSHOTS
            WHERE IS_ACTIVE AND PUBLICATION_STATUS = 'ACTIVE'
            """,
        )
        return row is not None and row[0] == expected
    except Exception:
        return False


def _project_schemas_ready(connection: Any) -> bool:
    row = _query_one(
        connection,
        """
        SELECT COUNT(*)
        FROM COVID_ANALYTICS.INFORMATION_SCHEMA.SCHEMATA
        WHERE SCHEMA_NAME IN ('RAW', 'STAGING', 'MARTS', 'APP')
        """,
    )
    return row is not None and int(row[0]) == 4


def _compose_services_running() -> bool:
    result = _run_command(
        ["docker", "compose", "ps", "--services", "--status", "running"],
        check=False,
    )
    running = set(result.stdout.split())
    return {"api", "dashboard", "mongo", "redis"}.issubset(running)


def _http_request(url: str) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read()
            payload = json.loads(body) if "json" in content_type else body
            return response.status, payload
    except urllib.error.HTTPError as exc:
        content_type = exc.headers.get("Content-Type", "")
        body = exc.read()
        try:
            payload = json.loads(body) if "json" in content_type else body
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload


def _api_failure(url: str, status: int, payload: Any) -> BootstrapError:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    code = str(error.get("code", "dependency_unavailable"))
    request_id = error.get("request_id")
    references = [f"HTTP {status} from {url}", f"error code: {code}"]
    if request_id:
        references.append(f"request ID: {request_id}")
    guidance = {
        "cache_unavailable": (
            "Redis is unavailable, so the API blocked Snowflake queries to protect credits.",
            (
                "Run docker compose ps and confirm redis is healthy.",
                "Restart the project services after Redis is healthy; do not bypass the cache protection.",
            ),
        ),
        "mongodb_unavailable": (
            "MongoDB is unavailable or its configured credentials no longer match the existing volume.",
            (
                "Run docker compose ps and confirm mongo is healthy.",
                "If credentials changed after the volume was created, restore the old .env value or follow the README's explicit destructive reset option.",
            ),
        ),
        "snowflake_authentication_failed": (
            "Snowflake rejected the application's configured user credentials.",
            ("Correct the Snowflake username/password in .env and restart the API.",),
        ),
        "snowflake_account_invalid": (
            "The application's Snowflake account identifier is invalid.",
            ("Use the organization-account connector value in SNOWFLAKE_ACCOUNT.",),
        ),
        "snowflake_role_unauthorized": (
            "The configured API user cannot use COVID_APP_ROLE.",
            ("Rerun setup with --resume so the secure role grant can be verified.",),
        ),
        "snowflake_warehouse_unavailable": (
            "COVID_WH is missing, suspended without resume permission, or inaccessible to COVID_APP_ROLE.",
            ("Rerun setup with --resume and preserve COVID_APP_ROLE as the API role.",),
        ),
        "analytics_objects_missing": (
            "The required MARTS objects have not been created or granted to COVID_APP_ROLE.",
            ("Rerun setup with --resume to create and verify the marts.",),
        ),
    }
    likely_cause, fixes = guidance.get(
        code,
        (
            "A required API dependency is not ready.",
            (
                "Run docker compose ps, then inspect only the affected service with docker compose logs <service>.",
                "Use the request ID below to correlate the sanitized application logs.",
            ),
        ),
    )
    return BootstrapError(
        "An application verification endpoint returned a dependency error.",
        likely_cause=likely_cause,
        fixes=fixes,
        technical_reference="; ".join(references),
    )


def _is_structured_dependency_failure(status: int, payload: Any) -> bool:
    if status != 503 or not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return isinstance(error, dict) and bool(error.get("code"))


def poll_http_postconditions(
    *,
    include_snowflake: bool,
    timeout_seconds: int = 120,
    api_base_url: str = "http://localhost:8000",
    dashboard_base_url: str = "http://localhost:8050",
) -> None:
    checks: list[tuple[str, Callable[[int, Any], bool]]] = [
        (f"{api_base_url}/health/live", lambda status, _: status == 200),
        (f"{api_base_url}/health/ready", lambda status, _: status == 200),
    ]
    if include_snowflake:
        checks.extend(
            [
                (
                    f"{api_base_url}/health/snowflake",
                    lambda status, _: status == 200,
                ),
                (
                    f"{api_base_url}/dashboard/overview",
                    lambda status, _: status == 200,
                ),
                (
                    f"{api_base_url}/countries",
                    lambda status, payload: (
                        status == 200
                        and isinstance(payload, list)
                        and len(payload) >= 1
                    ),
                ),
            ]
        )
    checks.append((f"{dashboard_base_url}/overview", lambda status, _: status == 200))
    deadline = time.monotonic() + timeout_seconds
    pending = {url: validator for url, validator in checks}
    last_responses: dict[str, tuple[int, Any]] = {}
    while pending and time.monotonic() < deadline:
        for url, validator in list(pending.items()):
            try:
                status, payload = _http_request(url)
                last_responses[url] = (status, payload)
                if validator(status, payload):
                    pending.pop(url)
                elif _is_structured_dependency_failure(status, payload):
                    raise _api_failure(url, status, payload)
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                continue
        if pending:
            time.sleep(2)
    if pending:
        details = []
        for url in sorted(pending):
            status = last_responses.get(url, ("no response", None))[0]
            details.append(f"{url} ({status})")
        raise BootstrapError(
            "Timed out waiting for: " + ", ".join(details),
            likely_cause="A container is still starting, unhealthy, or cannot reach another Compose service.",
            fixes=(
                "Run docker compose ps to identify the unhealthy service.",
                "Run docker compose logs api dashboard mongo redis and inspect the first dependency failure.",
                "If only the dashboard fails, confirm its in-container API URL is http://api:8000, while the browser uses http://localhost:8000.",
            ),
        )


def _http_postconditions_pass(
    *,
    include_snowflake: bool,
    api_base_url: str = "http://localhost:8000",
    dashboard_base_url: str = "http://localhost:8050",
) -> bool:
    """Recheck HTTP postconditions without turning a resume probe into a failure."""
    try:
        poll_http_postconditions(
            include_snowflake=include_snowflake,
            timeout_seconds=15,
            api_base_url=api_base_url,
            dashboard_base_url=dashboard_base_url,
        )
    except BootstrapError:
        return False
    return True


def _initialize_mongodb() -> None:
    result = _run_command(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "scripts.setup_mongodb",
        ],
        check=False,
    )
    if result.returncode != 0:
        raise BootstrapError(
            "MongoDB index setup could not authenticate or complete.",
            likely_cause="MongoDB is unhealthy, or the existing Docker volume was initialized with different root credentials.",
            fixes=(
                "Confirm mongo is healthy with docker compose ps.",
                "If the password changed, restore the original MONGO_ROOT_PASSWORD and matching MONGODB_URI in .env.",
                "Only if local MongoDB data may be permanently deleted, follow the README's explicit volume-reset command. Setup never deletes volumes automatically.",
            ),
        )


def _verify_dashboard_api_bridge() -> None:
    result = _run_command(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "dashboard",
            "python",
            "-c",
            (
                "import urllib.request; "
                "response=urllib.request.urlopen("
                "'http://api:8000/health/live', timeout=5); "
                "raise SystemExit(0 if response.status == 200 else 1)"
            ),
        ],
        check=False,
    )
    if result.returncode != 0:
        raise BootstrapError(
            "The dashboard container cannot reach the API container.",
            likely_cause="The dashboard's internal API URL is incorrect, or the API service is unhealthy on the Compose network.",
            fixes=(
                "Keep DASHBOARD_API_BASE_URL=http://api:8000 for container-to-container traffic.",
                "Use http://localhost:8000 only from the host browser or terminal.",
                "Run docker compose ps and docker compose logs api dashboard.",
            ),
        )


def _mongodb_indexes_ready(values: dict[str, str]) -> bool:
    client = MongoClient(
        values["MONGODB_URI"],
        serverSelectionTimeoutMS=3000,
    )
    try:
        client.admin.command("ping")
        collection = client[values["MONGO_DATABASE"]]["annotations"]
        indexes = collection.index_information()
        expected = {
            "annotations_country_date_created_at": [
                ("country", 1),
                ("report_date", 1),
                ("created_at", 1),
            ],
            "annotations_country_metric_date": [
                ("country", 1),
                ("metric", 1),
                ("report_date", 1),
            ],
        }
        return all(
            indexes.get(name, {}).get("key") == keys for name, keys in expected.items()
        )
    except Exception:
        return False
    finally:
        client.close()


def setup(
    *,
    resume: bool,
    non_interactive: bool,
    audit_path: Path,
    container_data_only: bool = False,
) -> None:
    state = SetupState()
    if container_data_only:
        doctor_container_workspace()
    else:
        _setup_progress(
            1,
            "Validate local prerequisites",
            "Checking uv, the Docker engine and Compose plugin, repository write access, secret ignores, and ports 8000/8050/27017.",
        )
        doctor_local(offer_restart=True, non_interactive=non_interactive)
    _setup_progress(
        2,
        "Configure and validate Snowflake access",
        "Collecting only missing values, writing the ignored .env atomically, authenticating with ACCOUNTADMIN, and validating the Marketplace ECDC source.",
    )
    values = configure_environment(non_interactive=non_interactive)
    doctor_configured(values)
    context = _setup_context(values)
    state.set_context(context)
    doctor_inputs = [REPOSITORY_ROOT / "compose.yaml"]
    if container_data_only:
        doctor_inputs.append(REPOSITORY_ROOT / "compose.setup.yaml")
    state.mark_complete(
        "local_doctor",
        _input_checksum(
            *doctor_inputs,
            values=("container-workspace-v1" if container_data_only else "doctor-v1",),
        ),
    )
    state.mark_complete(
        "environment_and_configured_doctor",
        _input_checksum(ENV_EXAMPLE_PATH, values=(context["snowflake_account_hash"],)),
    )

    bootstrap_connection = connect_snowflake(
        values,
        role=values.get("SNOWFLAKE_BOOTSTRAP_ROLE", "ACCOUNTADMIN"),
        include_project_context=False,
    )
    try:
        _setup_progress(
            3,
            "Create account-level project objects",
            "Creating the monitored warehouse and project roles, then granting COVID_PROJECT_ADMIN and COVID_APP_ROLE to the configured user.",
        )

        _run_step(
            state=state,
            context=context,
            name="account_setup_and_role_grants",
            checksum=_input_checksum(SQL_FILES["account_setup"]),
            action=lambda: bootstrap_account_objects_and_roles(
                bootstrap_connection,
            ),
            postcondition=lambda: (
                _object_exists(bootstrap_connection, "ROLES", "COVID_PROJECT_ADMIN")
                and _object_exists(bootstrap_connection, "ROLES", "COVID_APP_ROLE")
                and _current_user_has_roles(
                    bootstrap_connection,
                    {"COVID_PROJECT_ADMIN", "COVID_APP_ROLE"},
                )
            ),
            resume=resume,
        )
    finally:
        bootstrap_connection.close()

    # RAW does not exist until 00_project_objects.sql runs on a clean account.
    # Connect with only the freshly granted role; the SQL file creates and then
    # selects the project warehouse, database, and schema in the required order.
    admin_connection = connect_snowflake(
        values,
        role=values["SNOWFLAKE_ROLE"],
        include_project_context=False,
    )
    try:
        _setup_progress(
            4,
            "Create project schemas and build staging",
            "Reconnecting as COVID_PROJECT_ADMIN, creating project schemas, verifying Marketplace access, and building the daily staging objects.",
        )

        _run_step(
            state=state,
            context=context,
            name="project_objects",
            checksum=_input_checksum(SQL_FILES["project_objects"]),
            action=lambda: execute_sql_file(
                admin_connection,
                SQL_FILES["project_objects"],
            ),
            postcondition=lambda: _project_schemas_ready(admin_connection),
            resume=resume,
        )

        def verify_marketplace_access() -> bool:
            try:
                row = _query_one(
                    admin_connection,
                    "SELECT 1 FROM COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL LIMIT 1",
                )
                return row is not None
            except snowflake.connector.Error as exc:
                logger.exception(
                    "marketplace_access_failed",
                    extra={"error_type": type(exc).__name__},
                    exc_info=sanitized_exception_info(exc),
                )
                raise BootstrapError(
                    "The Marketplace ECDC_GLOBAL source is not accessible to the project role.",
                    likely_cause="The Marketplace database is missing, has a different name, or its imported privileges are unavailable.",
                    fixes=(
                        "Add the listing as COVID19_EPIDEMIOLOGICAL_DATA and verify PUBLIC.ECDC_GLOBAL in Snowsight.",
                        "Then rerun setup with --resume; completed account-level work will be rechecked and skipped safely.",
                    ),
                ) from exc

        _run_step(
            state=state,
            context=context,
            name="marketplace_access",
            checksum=_input_checksum(values=(values["SNOWFLAKE_WAREHOUSE"],)),
            action=lambda: _execute(admin_connection, "USE WAREHOUSE COVID_WH"),
            postcondition=verify_marketplace_access,
            resume=resume,
        )
        _run_step(
            state=state,
            context=context,
            name="mapping_and_staging",
            checksum=_input_checksum(SQL_FILES["mapping"], SQL_FILES["staging"]),
            action=lambda: (
                execute_sql_file(admin_connection, SQL_FILES["mapping"]),
                execute_sql_file(admin_connection, SQL_FILES["staging"]),
            ),
            postcondition=lambda: (
                _query_one(
                    admin_connection,
                    "SELECT COUNT(*) FROM COVID_ANALYTICS.RAW.COUNTRY_CODE_MAPPING",
                )[0]
                >= 1
                and _query_one(
                    admin_connection,
                    "SELECT COUNT(*) FROM COVID_ANALYTICS.STAGING.COVID_COUNTRY_DAILY",
                )[0]
                >= 1
            ),
            resume=resume,
        )
        _setup_progress(
            5,
            "Load versioned World Bank context",
            "Publishing the checksum-valid committed WDI snapshot while preserving the legacy 2020 population snapshot used to seed the frozen COVID denominator. Guided setup makes no World Bank network request.",
        )

        def refresh_population_safely() -> None:
            try:
                # The frozen denominator is governed Snowflake state. Reloading its
                # legacy source only to create a local receipt would weaken that
                # boundary and make clean Codespaces behave differently from reuse.
                if not _frozen_denominator_ready(admin_connection):
                    refresh_population(
                        connection=admin_connection,
                        csv_path=POPULATION_SNAPSHOT_PATH,
                        source_manifest_path=POPULATION_SOURCE_MANIFEST_PATH,
                        manifest_path=POPULATION_MANIFEST_PATH,
                        source_mode="snapshot",
                    )
                publish_wdi_snapshot(
                    admin_connection,
                    WDI_SNAPSHOT_PATH,
                    WDI_SNAPSHOT_MANIFEST_PATH,
                )
            except Exception as exc:
                logger.exception(
                    "population_refresh_failed",
                    extra={"error_type": type(exc).__name__},
                    exc_info=sanitized_exception_info(exc),
                )
                raise BootstrapError(
                    "World Bank snapshot validation or publication failed.",
                    likely_cause="A committed snapshot or checksum manifest is missing/modified, or the Snowflake publication transaction was unavailable.",
                    fixes=(
                        "Restore both committed World Bank CSV files and their manifests from Git, then retry.",
                        "If the failure repeats, use the audit reference to identify the failed validation stage.",
                    ),
                ) from exc

        world_bank_publication_checksum = _world_bank_publication_checksum()
        _run_step(
            state=state,
            context=context,
            name="world_bank_snapshot_publication",
            checksum=world_bank_publication_checksum,
            action=refresh_population_safely,
            postcondition=lambda: (
                (
                    _frozen_denominator_ready(admin_connection)
                    or _population_matches(admin_connection)
                )
                and _wdi_snapshot_matches(admin_connection)
            ),
            resume=resume,
        )

        _setup_progress(
            6,
            "Create and verify analytical marts",
            "Selecting the active WDI snapshot, freezing the existing COVID denominator, and rebuilding baseline, annual, enriched, latest and context marts in dependency order.",
        )

        def create_marts() -> None:
            ordered_keys = (
                "population_verify",
                "world_bank_context",
                "mart",
                "reporting",
            )
            snapshot_id = json.loads(
                WDI_SNAPSHOT_MANIFEST_PATH.read_text(encoding="utf-8")
            )["snapshot_id"]
            try:
                for key in ordered_keys:
                    execute_sql_file(admin_connection, SQL_FILES[key])
            except Exception:
                restored = rollback_wdi_snapshot(admin_connection, snapshot_id)
                if restored:
                    logger.warning(
                        "world_bank_snapshot_rolled_back",
                        extra={
                            "failed_snapshot_id": snapshot_id,
                            "restored_snapshot_id": restored,
                        },
                    )
                    for key in ("world_bank_context", "mart", "reporting"):
                        execute_sql_file(admin_connection, SQL_FILES[key])
                raise

        _run_step(
            state=state,
            context=context,
            name="population_verification_and_marts",
            checksum=_analytical_marts_checksum(world_bank_publication_checksum),
            action=create_marts,
            postcondition=lambda: (
                (
                    _frozen_denominator_ready(admin_connection)
                    or _population_matches(admin_connection)
                )
                and _snowflake_objects_ready(admin_connection)
                and _wdi_snapshot_matches(admin_connection)
            ),
            resume=resume,
        )
    finally:
        admin_connection.close()

    if container_data_only:
        console("\nSNOWFLAKE AND CONFIGURATION PHASE COMPLETED")
        console("The host launcher will now start and verify the Docker services.")
        console(f"Phase audit log: {audit_path}")
        return

    _setup_progress(
        7,
        "Build and start application services",
        "Validating Compose configuration, then building and starting FastAPI, Dash, MongoDB, and Redis without deleting existing volumes.",
    )
    _run_step(
        state=state,
        context=context,
        name="docker_services",
        checksum=_input_checksum(REPOSITORY_ROOT / "compose.yaml"),
        action=lambda: (
            _run_command(["docker", "compose", "config", "--quiet"]),
            _run_command(
                ["docker", "compose", "up", "--build", "-d"],
                capture_output=False,
            ),
        ),
        postcondition=_compose_services_running,
        resume=resume,
    )
    _setup_progress(
        8,
        "Create MongoDB indexes",
        "Creating the annotations collection and its non-unique indexes idempotently with the configured local credentials.",
    )
    _run_step(
        state=state,
        context=context,
        name="mongodb_indexes",
        checksum=_input_checksum(REPOSITORY_ROOT / "scripts" / "setup_mongodb.py"),
        action=_initialize_mongodb,
        postcondition=lambda: _mongodb_indexes_ready(values),
        resume=resume,
    )
    _setup_progress(
        9,
        "Verify the finished application",
        "Checking container networking, API liveness/readiness, explicit Snowflake access, analytical data, and the dashboard before reporting success.",
    )
    _run_step(
        state=state,
        context=context,
        name="smoke_tests",
        checksum=_input_checksum(
            REPOSITORY_ROOT / "compose.yaml", values=("smoke-v1",)
        ),
        action=lambda: (
            _verify_dashboard_api_bridge(),
            poll_http_postconditions(include_snowflake=True),
        ),
        postcondition=lambda: _http_postconditions_pass(include_snowflake=True),
        resume=resume,
    )
    _show_setup_success(audit_path)


def finalize_container_setup(*, resume: bool, audit_path: Path) -> None:
    """Finalize setup from the API container without controlling host Docker."""
    values = load_runtime_environment()
    state = SetupState()
    context = _setup_context(values)
    state.set_context(context)
    api_base_url = "http://127.0.0.1:8000"
    dashboard_base_url = "http://dashboard:8050"

    _setup_progress(
        7,
        "Confirm application services",
        "Checking API liveness plus MongoDB and Redis readiness inside the Compose network.",
    )
    _run_step(
        state=state,
        context=context,
        name="docker_services",
        checksum=_input_checksum(
            REPOSITORY_ROOT / "compose.yaml",
            REPOSITORY_ROOT / "compose.setup.yaml",
            values=("container-runtime-v1",),
        ),
        action=lambda: poll_http_postconditions(
            include_snowflake=False,
            api_base_url=api_base_url,
            dashboard_base_url=dashboard_base_url,
        ),
        postcondition=lambda: _http_postconditions_pass(
            include_snowflake=False,
            api_base_url=api_base_url,
            dashboard_base_url=dashboard_base_url,
        ),
        resume=resume,
    )

    _setup_progress(
        8,
        "Verify MongoDB indexes",
        "Confirming the annotation indexes created by the Docker launcher are present and usable.",
    )
    _run_step(
        state=state,
        context=context,
        name="mongodb_indexes",
        checksum=_input_checksum(
            REPOSITORY_ROOT / "scripts" / "setup_mongodb.py",
            values=("container-runtime-v1",),
        ),
        action=lambda: None,
        postcondition=lambda: _mongodb_indexes_ready(values),
        resume=resume,
    )

    _setup_progress(
        9,
        "Verify the finished application",
        "Checking explicit Snowflake access, cached analytical data, and the dashboard before reporting success.",
    )
    _run_step(
        state=state,
        context=context,
        name="smoke_tests",
        checksum=_input_checksum(
            REPOSITORY_ROOT / "compose.yaml",
            values=("container-smoke-v1",),
        ),
        action=lambda: poll_http_postconditions(
            include_snowflake=True,
            api_base_url=api_base_url,
            dashboard_base_url=dashboard_base_url,
        ),
        postcondition=lambda: _http_postconditions_pass(
            include_snowflake=True,
            api_base_url=api_base_url,
            dashboard_base_url=dashboard_base_url,
        ),
        resume=resume,
    )
    _show_setup_success(audit_path)


def verify() -> None:
    values = load_configured_environment()
    doctor_configured(values)
    connection = connect_snowflake(values, role=values["SNOWFLAKE_ROLE"])
    try:
        if not (
            _frozen_denominator_ready(connection) or _population_matches(connection)
        ) or not _snowflake_objects_ready(connection):
            raise BootstrapError("Snowflake postconditions are incomplete.")
    finally:
        connection.close()
    if not _compose_services_running():
        raise BootstrapError(
            "Required Docker services are not running.",
            likely_cause="The application has not been started, or one of api, dashboard, mongo, or redis stopped.",
            fixes=(
                f"Start the existing services with {_start_command()}.",
                "Run docker compose ps to identify any unhealthy service.",
            ),
        )
    _verify_dashboard_api_bridge()
    poll_http_postconditions(include_snowflake=True)
    console("Verification passed.")
    _show_urls()


def _show_urls() -> None:
    console("Dashboard overview: http://localhost:8050/overview")
    console("Country explorer: http://localhost:8050/country")
    console("Country comparison: http://localhost:8050/compare")
    console("Annotations: http://localhost:8050/annotations")
    console("API documentation: http://localhost:8000/docs")


def start() -> None:
    doctor_local()
    load_configured_environment()
    _run_command(["docker", "compose", "config", "--quiet"])
    _run_command(["docker", "compose", "up", "-d"], capture_output=False)
    poll_http_postconditions(include_snowflake=False)
    _verify_dashboard_api_bridge()
    console("\nSERVICES STARTED SUCCESSFULLY")
    console("=" * 29)
    console("FastAPI, Dash, MongoDB, and Redis passed the cheap startup checks.")
    console(
        "Snowflake was not queried during start; use its explicit health check only when needed."
    )
    _show_urls()
    console(f"Stop safely with: {_stop_command()}")


def stop() -> None:
    _run_command(["docker", "compose", "stop"], capture_output=False)
    console("\nSERVICES STOPPED SAFELY")
    console(
        "MongoDB and Redis volumes were preserved; Snowflake objects were unchanged."
    )
    console(f"Start again with: {_start_command()}")


def analyze() -> None:
    values = load_configured_environment()
    connection = connect_snowflake(values, role=values["SNOWFLAKE_ROLE"])
    try:
        execute_sql_file(connection, SQL_FILES["exploration"])
        execute_sql_file(connection, SQL_FILES["analysis"])
    finally:
        connection.close()
    _run_command(
        ["uv", "run", "--locked", "python", "scripts/run_eda.py"],
        capture_output=False,
    )
    console("Optional analysis completed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely bootstrap and operate the COVID analytics platform."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor")
    doctor_mode = doctor.add_mutually_exclusive_group(required=True)
    doctor_mode.add_argument("--local", action="store_true")
    doctor_mode.add_argument("--configured", action="store_true")
    for command in ("setup", "setup-data"):
        setup_parser = subparsers.add_parser(command)
        setup_parser.add_argument("--resume", action="store_true")
        setup_parser.add_argument("--non-interactive", action="store_true")
    finalizer = subparsers.add_parser("finalize-container-setup")
    finalizer.add_argument("--resume", action="store_true")
    for command in ("verify", "start", "stop", "analyze"):
        subparsers.add_parser(command)
    return parser.parse_args()


def _default_retry_command(command: str) -> str:
    if command in {"setup", "setup-data", "finalize-container-setup"}:
        return _setup_command(resume=True)
    return f"uv run --locked python -m scripts.bootstrap {command}"


def _report_preserved_container_state() -> None:
    """Report recoverable Docker state without stopping or deleting anything."""
    console("Containers and Docker volumes were left unchanged.", error=True)
    if shutil.which("docker"):
        try:
            result = _run_command(
                ["docker", "compose", "ps"],
                check=False,
            )
            if result.stdout.strip():
                console(result.stdout.strip(), error=True)
        except (OSError, BootstrapError) as exc:
            logger.exception(
                "container_state_report_failed",
                exc_info=sanitized_exception_info(exc),
            )
    console(
        "Inspect service logs with: docker compose logs api dashboard mongo redis",
        error=True,
    )
    console(
        f"To stop the preserved containers safely, run: {_stop_command()}", error=True
    )


def main() -> None:
    args = parse_args()
    audit_path = configure_audit_logging(args.command)
    try:
        if args.command == "doctor":
            if args.local:
                doctor_local()
            else:
                doctor_configured(load_configured_environment())
            console("Doctor checks passed.")
        elif args.command in {"setup", "setup-data"}:
            setup(
                resume=args.resume,
                non_interactive=args.non_interactive,
                audit_path=audit_path,
                container_data_only=args.command == "setup-data",
            )
        elif args.command == "finalize-container-setup":
            finalize_container_setup(
                resume=args.resume,
                audit_path=audit_path,
            )
        elif args.command == "verify":
            verify()
        elif args.command == "start":
            start()
        elif args.command == "stop":
            stop()
        else:
            analyze()
    except KeyboardInterrupt:
        logger.warning("bootstrap_interrupted", extra={"audit_path": str(audit_path)})
        _flush_log_handlers()
        interrupted = BootstrapError(
            "Setup was interrupted from the terminal (exit status 130).",
            likely_cause="Ctrl+C or another keyboard interrupt stopped the current operation.",
            fixes=(
                "Review the preserved container state shown below.",
                "No Docker volume was deleted; completed setup steps remain resumable after their postconditions are rechecked.",
            ),
            retry=_setup_command(resume=True),
            technical_reference=str(audit_path),
        )
        _show_failure(
            interrupted,
            audit_path=audit_path,
            default_retry=_setup_command(resume=True),
        )
        _report_preserved_container_state()
        raise SystemExit(130) from None
    except BootstrapError as exc:
        logger.exception(
            "bootstrap_failed",
            extra={"error_type": type(exc).__name__, "audit_path": str(audit_path)},
            exc_info=sanitized_exception_info(exc),
        )
        _flush_log_handlers()
        default_retry = _default_retry_command(args.command)
        _show_failure(exc, audit_path=audit_path, default_retry=default_retry)
        _report_preserved_container_state()
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.exception(
            "bootstrap_failed",
            extra={"error_type": type(exc).__name__, "audit_path": str(audit_path)},
            exc_info=sanitized_exception_info(exc),
        )
        _flush_log_handlers()
        if isinstance(exc, snowflake.connector.Error):
            failure = _snowflake_bootstrap_error(
                exc,
                role="the configured setup role",
            )
        else:
            failure = BootstrapError(
                "An external dependency returned an unexpected error; technical details were redacted.",
                likely_cause="Docker, Snowflake, MongoDB, Redis, the network, or a local file operation failed outside a recognized contract.",
                fixes=(
                    "Review the technical reference and the closest troubleshooting section in README.md.",
                    "Correct the dependency problem, then resume; do not paste secrets from .env into an issue or log.",
                ),
            )
        _show_failure(
            failure,
            audit_path=audit_path,
            default_retry=_default_retry_command(args.command),
        )
        _report_preserved_container_state()
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
