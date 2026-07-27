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
from scripts.load_population import DEFAULT_MANIFEST_PATH, refresh_population

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_PATH = REPOSITORY_ROOT / ".env.example"
ENV_PATH = REPOSITORY_ROOT / ".env"
STATE_PATH = REPOSITORY_ROOT / ".setup-state.json"
SETUP_OUTPUT_ROOT = REPOSITORY_ROOT / "outputs" / "setup"
POPULATION_MANIFEST_PATH = REPOSITORY_ROOT / DEFAULT_MANIFEST_PATH
COMPOSE_PROJECT_NAME = "covid-platform"
REQUIRED_PORTS = (8000, 8050, 27017)
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
    "MONGO_ROOT_USERNAME",
    "MONGO_ROOT_PASSWORD",
    "MONGO_DATABASE",
    "MONGODB_URI",
    "REDIS_URL",
)
SQL_FILES = {
    "account_setup": REPOSITORY_ROOT / "sql" / "00_project_setup.sql",
    "mapping": REPOSITORY_ROOT / "sql" / "02_create_country_mapping.sql",
    "staging": REPOSITORY_ROOT / "sql" / "03_create_staging_view.sql",
    "population_verify": REPOSITORY_ROOT / "sql" / "04_verify_population_data.sql",
    "mart": REPOSITORY_ROOT / "sql" / "05_create_enriched_view.sql",
    "reporting": REPOSITORY_ROOT / "sql" / "06_create_reporting_objects.sql",
    "exploration": REPOSITORY_ROOT / "sql" / "01_data_exploration.sql",
    "analysis": REPOSITORY_ROOT / "sql" / "07_analysis_queries.sql",
}
logger = logging.getLogger(__name__)


class BootstrapError(RuntimeError):
    """A sanitized, user-actionable bootstrap failure."""


class BootstrapAuditFilter(logging.Filter):
    """Keep third-party connector records out of the persistent audit file."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in {
            "__main__",
            "scripts.bootstrap",
            "scripts.load_population",
        }


def console(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(f"{message}\n")
    stream.flush()


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_checksum(*paths: Path, values: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path.relative_to(REPOSITORY_ROOT)).encode("utf-8"))
        digest.update(bytes.fromhex(_file_sha256(path)))
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def configure_audit_logging(command: str) -> Path:
    configure_logging("project-bootstrap", os.getenv("LOG_LEVEL", "INFO"))
    for external_logger in ("snowflake.connector", "urllib3", "pymongo"):
        logging.getLogger(external_logger).setLevel(logging.CRITICAL)
    SETUP_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = SETUP_OUTPUT_ROOT / f"{command}-{timestamp}-{secrets.token_hex(3)}.jsonl"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ServiceFilter("project-bootstrap"))
    handler.addFilter(BootstrapAuditFilter())
    logging.getLogger().addHandler(handler)
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

    prompt_keys = {
        "SNOWFLAKE_ACCOUNT": "Snowflake account (organization-account): ",
        "SNOWFLAKE_USER": "Snowflake username: ",
    }
    for key, prompt in prompt_keys.items():
        if _is_placeholder(key, values.get(key)):
            entered = input(prompt).strip()
            if not entered:
                raise BootstrapError(f"{key} is required.")
            values[key] = entered
    if _is_placeholder("SNOWFLAKE_PASSWORD", values.get("SNOWFLAKE_PASSWORD")):
        password = getpass.getpass("Snowflake password: ")
        if not password:
            raise BootstrapError("SNOWFLAKE_PASSWORD is required.")
        values["SNOWFLAKE_PASSWORD"] = password
    if _is_placeholder("MONGO_ROOT_PASSWORD", values.get("MONGO_ROOT_PASSWORD")):
        mongo_password = getpass.getpass(
            "MongoDB root password (leave blank to generate one): "
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
        raise BootstrapError("Docker CLI is not installed or is not available on PATH.")
    _run_command(["docker", "info"])
    _run_command(["docker", "compose", "version"])
    _verify_gitignore_contract()
    with tempfile.NamedTemporaryFile(dir=REPOSITORY_ROOT, delete=True):
        pass
    occupied = {port for port in REQUIRED_PORTS if _port_is_open(port)}
    project_ports = _compose_port_owners() if occupied else set()
    unrelated = occupied - project_ports
    if unrelated:
        raise BootstrapError(
            "Required ports are used by another process: "
            + ", ".join(str(port) for port in sorted(unrelated))
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
    return snowflake.connector.connect(
        **parameters,
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
                    "Snowflake Marketplace database COVID19_EPIDEMIOLOGICAL_DATA is missing."
                )
        finally:
            cursor.close()
    finally:
        connection.close()
    logger.info("doctor_configured_passed")


def execute_sql_file(connection: Any, path: Path) -> None:
    logger.info("sql_file_started", extra={"sql_file": path.name})
    statement_count = 0
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
    logger.info(
        "sql_file_completed",
        extra={"sql_file": path.name, "statement_count": statement_count},
    )


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
        granted_roles = {str(grant[1]).upper() for grant in cursor.fetchall()}
        return {role.upper() for role in expected_roles}.issubset(granted_roles)
    finally:
        cursor.close()


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


def _snowflake_objects_ready(connection: Any) -> bool:
    checks = (
        "SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED",
        "SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS",
    )
    return all(_query_one(connection, query) is not None for query in checks)


def _compose_services_running() -> bool:
    result = _run_command(
        ["docker", "compose", "ps", "--services", "--status", "running"],
        check=False,
    )
    running = set(result.stdout.split())
    return {"api", "dashboard", "mongo", "redis"}.issubset(running)


def _http_request(url: str) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        content_type = response.headers.get("Content-Type", "")
        body = response.read()
        payload = json.loads(body) if "json" in content_type else body
        return response.status, payload


def poll_http_postconditions(
    *, include_snowflake: bool, timeout_seconds: int = 120
) -> None:
    checks: list[tuple[str, Callable[[int, Any], bool]]] = [
        ("http://localhost:8000/health/live", lambda status, _: status == 200),
        ("http://localhost:8000/health/ready", lambda status, _: status == 200),
    ]
    if include_snowflake:
        checks.extend(
            [
                (
                    "http://localhost:8000/health/snowflake",
                    lambda status, _: status == 200,
                ),
                (
                    "http://localhost:8000/dashboard/overview",
                    lambda status, _: status == 200,
                ),
                (
                    "http://localhost:8000/countries",
                    lambda status, payload: (
                        status == 200
                        and isinstance(payload, list)
                        and len(payload) >= 1
                    ),
                ),
            ]
        )
    checks.append(("http://localhost:8050/overview", lambda status, _: status == 200))
    deadline = time.monotonic() + timeout_seconds
    pending = {url: validator for url, validator in checks}
    while pending and time.monotonic() < deadline:
        for url, validator in list(pending.items()):
            try:
                status, payload = _http_request(url)
                if validator(status, payload):
                    pending.pop(url)
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                continue
        if pending:
            time.sleep(2)
    if pending:
        raise BootstrapError("Timed out waiting for: " + ", ".join(sorted(pending)))


def _http_postconditions_pass(*, include_snowflake: bool) -> bool:
    """Recheck HTTP postconditions without turning a resume probe into a failure."""
    try:
        poll_http_postconditions(
            include_snowflake=include_snowflake,
            timeout_seconds=15,
        )
    except BootstrapError:
        return False
    return True


def _initialize_mongodb() -> None:
    _run_command(
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
        capture_output=False,
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


def setup(*, resume: bool, non_interactive: bool) -> None:
    state = SetupState()
    doctor_local(offer_restart=True, non_interactive=non_interactive)
    values = configure_environment(non_interactive=non_interactive)
    doctor_configured(values)
    context = _setup_context(values)
    state.set_context(context)
    state.mark_complete(
        "local_doctor",
        _input_checksum(REPOSITORY_ROOT / "compose.yaml", values=("doctor-v1",)),
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

        def account_setup() -> None:
            execute_sql_file(bootstrap_connection, SQL_FILES["account_setup"])
            cursor = bootstrap_connection.cursor()
            try:
                cursor.execute("USE ROLE ACCOUNTADMIN")
                cursor.execute("SELECT CURRENT_USER()")
                current_user = cursor.fetchone()[0]
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

        _run_step(
            state=state,
            context=context,
            name="account_setup_and_role_grants",
            checksum=_input_checksum(SQL_FILES["account_setup"]),
            action=account_setup,
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

    admin_connection = connect_snowflake(values, role=values["SNOWFLAKE_ROLE"])
    try:

        def verify_marketplace_access() -> bool:
            row = _query_one(
                admin_connection,
                "SELECT 1 FROM COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL LIMIT 1",
            )
            return row is not None

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
        _run_step(
            state=state,
            context=context,
            name="population_refresh",
            checksum=_input_checksum(
                REPOSITORY_ROOT / "scripts" / "load_population.py",
                values=("2020",),
            ),
            action=lambda: refresh_population(
                connection=admin_connection,
                csv_path=REPOSITORY_ROOT
                / "data"
                / "external"
                / "world_bank_population_2020.csv",
                manifest_path=POPULATION_MANIFEST_PATH,
            ),
            postcondition=lambda: _population_matches(admin_connection),
            resume=resume,
        )

        def create_marts() -> None:
            for key in ("population_verify", "mart", "reporting"):
                execute_sql_file(admin_connection, SQL_FILES[key])

        _run_step(
            state=state,
            context=context,
            name="population_verification_and_marts",
            checksum=_input_checksum(
                SQL_FILES["population_verify"],
                SQL_FILES["mart"],
                SQL_FILES["reporting"],
                POPULATION_MANIFEST_PATH,
            ),
            action=create_marts,
            postcondition=lambda: (
                _population_matches(admin_connection)
                and _snowflake_objects_ready(admin_connection)
            ),
            resume=resume,
        )
    finally:
        admin_connection.close()

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
    _run_step(
        state=state,
        context=context,
        name="mongodb_indexes",
        checksum=_input_checksum(REPOSITORY_ROOT / "scripts" / "setup_mongodb.py"),
        action=_initialize_mongodb,
        postcondition=lambda: _mongodb_indexes_ready(values),
        resume=resume,
    )
    _run_step(
        state=state,
        context=context,
        name="smoke_tests",
        checksum=_input_checksum(
            REPOSITORY_ROOT / "compose.yaml", values=("smoke-v1",)
        ),
        action=lambda: poll_http_postconditions(include_snowflake=True),
        postcondition=lambda: _http_postconditions_pass(include_snowflake=True),
        resume=resume,
    )
    console("Setup completed successfully.")
    _show_urls()


def verify() -> None:
    values = load_configured_environment()
    doctor_configured(values)
    connection = connect_snowflake(values, role=values["SNOWFLAKE_ROLE"])
    try:
        if not _population_matches(connection) or not _snowflake_objects_ready(
            connection
        ):
            raise BootstrapError("Snowflake postconditions are incomplete.")
    finally:
        connection.close()
    if not _compose_services_running():
        raise BootstrapError("Required Docker services are not running.")
    poll_http_postconditions(include_snowflake=True)
    console("Verification passed.")


def _show_urls() -> None:
    console("API documentation: http://localhost:8000/docs")
    console("Dashboard: http://localhost:8050/overview")


def start() -> None:
    doctor_local()
    load_configured_environment()
    _run_command(["docker", "compose", "config", "--quiet"])
    _run_command(["docker", "compose", "up", "-d"], capture_output=False)
    poll_http_postconditions(include_snowflake=False)
    console("Services are ready.")
    _show_urls()


def stop() -> None:
    _run_command(["docker", "compose", "stop"], capture_output=False)
    console("Services stopped. Docker volumes were preserved.")


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
    setup_parser = subparsers.add_parser("setup")
    setup_parser.add_argument("--resume", action="store_true")
    setup_parser.add_argument("--non-interactive", action="store_true")
    for command in ("verify", "start", "stop", "analyze"):
        subparsers.add_parser(command)
    return parser.parse_args()


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
    console("To stop the preserved containers, run: docker compose down", error=True)


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
        elif args.command == "setup":
            setup(resume=args.resume, non_interactive=args.non_interactive)
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
        _report_preserved_container_state()
        console(
            f"Interrupted. No volumes were removed. Resume with setup --resume. Log: {audit_path}",
            error=True,
        )
        raise SystemExit(130) from None
    except BootstrapError as exc:
        logger.exception(
            "bootstrap_failed",
            extra={"error_type": type(exc).__name__, "audit_path": str(audit_path)},
            exc_info=sanitized_exception_info(exc),
        )
        console(f"Setup failed: {exc}", error=True)
        console(f"Audit log: {audit_path}", error=True)
        _report_preserved_container_state()
        console("Fix the issue and rerun setup with --resume.", error=True)
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.exception(
            "bootstrap_failed",
            extra={"error_type": type(exc).__name__, "audit_path": str(audit_path)},
            exc_info=sanitized_exception_info(exc),
        )
        console(
            "Setup failed because an external dependency returned an error. "
            f"Details were redacted. Audit log: {audit_path}",
            error=True,
        )
        _report_preserved_container_state()
        console("Fix the issue and rerun setup with --resume.", error=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
