from __future__ import annotations

import io
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from dotenv import dotenv_values

from scripts import bootstrap


class FakeStreamCursor:
    def __init__(self, query_id: str) -> None:
        self.sfqid = query_id
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeStreamConnection:
    def __init__(self) -> None:
        self.cursors = [FakeStreamCursor("one"), FakeStreamCursor("two")]
        self.remove_comments = None

    def execute_stream(self, stream, *, remove_comments: bool):
        stream.read()
        self.remove_comments = remove_comments
        return iter(self.cursors)


class FakeFailingStreamConnection:
    def __init__(self, error: Exception) -> None:
        self.cursor = FakeStreamCursor("completed-query")
        self.error = error

    def execute_stream(self, stream, *, remove_comments: bool):
        stream.read()
        yield self.cursor
        raise self.error


def _sql_without_comments(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )


class BootstrapTests(unittest.TestCase):
    def _temporary_root(self) -> Path:
        root = Path("outputs/test-bootstrap") / uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def test_dotenv_serialization_round_trips_special_characters(self) -> None:
        value = " space '#$value' \\ path "
        parsed = dotenv_values(
            stream=io.StringIO(f"VALUE={bootstrap._dotenv_quote(value)}\n")
        )
        self.assertEqual(parsed["VALUE"], value)

    def test_missing_checksum_input_reports_the_repository_path(self) -> None:
        missing = (self._temporary_root() / "missing.sql").resolve()

        with self.assertRaises(bootstrap.BootstrapError) as raised:
            bootstrap._input_checksum(missing)

        expected_path = bootstrap._repository_path_label(missing)
        self.assertIn(expected_path, str(raised.exception))
        self.assertEqual(raised.exception.technical_reference, expected_path)
        self.assertIn("Restore", raised.exception.fixes[0])

    def test_mart_checksum_uses_committed_world_bank_inputs_not_runtime_receipt(
        self,
    ) -> None:
        root = self._temporary_root().resolve()
        population_manifest = root / "population-source.manifest.json"
        wdi_manifest = root / "wdi-source.manifest.json"
        publication_support = root / "publication-support.py"
        mart_sql = root / "mart.sql"
        for path, content in (
            (population_manifest, "population-v1"),
            (wdi_manifest, "wdi-v1"),
            (publication_support, "loader-v1"),
            (mart_sql, "select 1"),
        ):
            path.write_text(content, encoding="utf-8")
        missing_runtime_receipt = root / "population-manifest.json"

        with (
            patch.object(
                bootstrap,
                "WORLD_BANK_PUBLICATION_INPUTS",
                (population_manifest, wdi_manifest, publication_support),
            ),
            patch.object(bootstrap, "ANALYTICAL_MART_INPUTS", (mart_sql,)),
            patch.object(
                bootstrap,
                "POPULATION_MANIFEST_PATH",
                missing_runtime_receipt,
            ),
        ):
            publication_v1 = bootstrap._world_bank_publication_checksum()
            marts_v1 = bootstrap._analytical_marts_checksum(publication_v1)
            self.assertFalse(missing_runtime_receipt.exists())

            population_manifest.write_text("population-v2", encoding="utf-8")
            publication_population_changed = (
                bootstrap._world_bank_publication_checksum()
            )
            marts_population_changed = bootstrap._analytical_marts_checksum(
                publication_population_changed
            )
            self.assertNotEqual(publication_v1, publication_population_changed)
            self.assertNotEqual(marts_v1, marts_population_changed)

            population_manifest.write_text("population-v1", encoding="utf-8")
            wdi_manifest.write_text("wdi-v2", encoding="utf-8")
            publication_wdi_changed = bootstrap._world_bank_publication_checksum()
            marts_wdi_changed = bootstrap._analytical_marts_checksum(
                publication_wdi_changed
            )
            self.assertNotEqual(publication_v1, publication_wdi_changed)
            self.assertNotEqual(marts_v1, marts_wdi_changed)

    def test_clean_workspace_reuses_frozen_denominator_without_runtime_receipt(
        self,
    ) -> None:
        root = self._temporary_root().resolve()
        missing_runtime_receipt = root / "population-manifest.json"
        values = {
            "SNOWFLAKE_ACCOUNT": "organization-account",
            "SNOWFLAKE_USER": "bootcamp-user",
            "SNOWFLAKE_DATABASE": "COVID_ANALYTICS",
            "SNOWFLAKE_BOOTSTRAP_ROLE": "ACCOUNTADMIN",
            "SNOWFLAKE_ROLE": "COVID_PROJECT_ADMIN",
            "SNOWFLAKE_WAREHOUSE": "COVID_WH",
        }
        bootstrap_connection = MagicMock()
        admin_connection = MagicMock()

        def run_selected_steps(**kwargs) -> None:
            if kwargs["name"] in {
                "world_bank_snapshot_publication",
                "population_verification_and_marts",
                "jhu_parallel_extension",
            }:
                kwargs["action"]()

        with (
            patch.object(
                bootstrap,
                "POPULATION_MANIFEST_PATH",
                missing_runtime_receipt,
            ),
            patch("scripts.bootstrap.SetupState", return_value=MagicMock()),
            patch("scripts.bootstrap.doctor_container_workspace"),
            patch("scripts.bootstrap.configure_environment", return_value=values),
            patch("scripts.bootstrap.doctor_configured"),
            patch(
                "scripts.bootstrap.connect_snowflake",
                side_effect=(bootstrap_connection, admin_connection),
            ),
            patch("scripts.bootstrap._setup_progress"),
            patch("scripts.bootstrap._run_step", side_effect=run_selected_steps),
            patch("scripts.bootstrap._frozen_denominator_ready", return_value=True),
            patch("scripts.bootstrap.refresh_population") as refresh_population,
            patch("scripts.bootstrap.publish_wdi_snapshot") as publish_wdi_snapshot,
            patch("scripts.bootstrap.execute_sql_file") as execute_sql_file,
            patch("scripts.bootstrap.console"),
        ):
            bootstrap.setup(
                resume=True,
                non_interactive=True,
                audit_path=root / "audit.jsonl",
                container_data_only=True,
            )

        self.assertFalse(missing_runtime_receipt.exists())
        refresh_population.assert_not_called()
        publish_wdi_snapshot.assert_called_once_with(
            admin_connection,
            bootstrap.WDI_SNAPSHOT_PATH,
            bootstrap.WDI_SNAPSHOT_MANIFEST_PATH,
        )
        self.assertEqual(
            [call.args[1] for call in execute_sql_file.call_args_list],
            [
                bootstrap.SQL_FILES["population_verify"],
                bootstrap.SQL_FILES["world_bank_context"],
                bootstrap.SQL_FILES["mart"],
                bootstrap.SQL_FILES["reporting"],
                bootstrap.SQL_FILES["jhu_extension"],
            ],
        )

    def test_state_resume_requires_context_checksum_and_postcondition(self) -> None:
        state = bootstrap.SetupState(self._temporary_root() / "state.json")
        context = {
            "snowflake_account_hash": "hash",
            "snowflake_user": "user",
            "database": "COVID_ANALYTICS",
        }
        state.set_context(context)
        state.mark_complete("mapping", "sha256:one")

        self.assertTrue(
            state.can_resume("mapping", "sha256:one", context, lambda: True)
        )
        self.assertFalse(
            state.can_resume("mapping", "sha256:two", context, lambda: True)
        )
        self.assertFalse(
            state.can_resume("mapping", "sha256:one", context, lambda: False)
        )

    def test_failed_postcondition_does_not_complete_step(self) -> None:
        state = bootstrap.SetupState(self._temporary_root() / "state.json")
        context = {
            "snowflake_account_hash": "hash",
            "snowflake_user": "user",
            "database": "COVID_ANALYTICS",
        }
        state.set_context(context)

        with self.assertRaisesRegex(bootstrap.BootstrapError, "Postcondition failed"):
            bootstrap._run_step(
                state=state,
                context=context,
                name="marts",
                checksum="sha256:one",
                action=lambda: None,
                postcondition=lambda: False,
                resume=False,
            )

        self.assertNotIn("marts", state.payload["completed_steps"])

    def test_execute_sql_file_uses_stream_api_and_closes_every_cursor(self) -> None:
        connection = FakeStreamConnection()
        sql_path = self._temporary_root() / "fixture.sql"
        sql_path.write_text("SELECT 1; SELECT 2;", encoding="utf-8")
        bootstrap.execute_sql_file(connection, sql_path)

        self.assertTrue(connection.remove_comments)
        self.assertTrue(all(cursor.closed for cursor in connection.cursors))

    def test_execute_sql_file_reports_the_failed_statement_without_raw_details(
        self,
    ) -> None:
        connector_error = bootstrap.snowflake.connector.ProgrammingError(
            msg="sensitive connector detail"
        )
        connection = FakeFailingStreamConnection(connector_error)
        sql_path = self._temporary_root() / "fixture.sql"
        sql_path.write_text("SELECT 1; SELECT 2;", encoding="utf-8")

        with self.assertRaises(bootstrap.BootstrapError) as raised:
            bootstrap.execute_sql_file(connection, sql_path)

        self.assertIn("statement 2", str(raised.exception))
        self.assertEqual(
            raised.exception.technical_reference,
            "fixture.sql: statement 2",
        )
        self.assertNotIn("sensitive connector detail", str(raised.exception))
        self.assertTrue(connection.cursor.closed)

    def test_account_setup_grants_user_before_project_role_sql(self) -> None:
        account_sql = bootstrap.SQL_FILES["account_setup"]
        project_sql = bootstrap.SQL_FILES["project_objects"]
        account_statements = _sql_without_comments(account_sql).upper()
        project_statements = _sql_without_comments(project_sql).upper()

        self.assertIn(
            "CREATE ROLE IF NOT EXISTS COVID_PROJECT_ADMIN", account_statements
        )
        self.assertGreaterEqual(account_statements.count("GENERATION = '2'"), 2)
        self.assertIn("ENABLE_QUERY_ACCELERATION = FALSE", account_statements)
        self.assertIn("AUTO_SUSPEND = 60", account_statements)
        self.assertNotIn("USE ROLE COVID_PROJECT_ADMIN", account_statements)
        self.assertIn(
            "GRANT IMPORTED PRIVILEGES\nON DATABASE COVID19_EPIDEMIOLOGICAL_DATA\nTO ROLE COVID_PROJECT_ADMIN",
            account_statements,
        )
        self.assertNotIn(
            "GRANT USAGE ON DATABASE COVID19_EPIDEMIOLOGICAL_DATA",
            account_statements,
        )
        self.assertNotIn(
            "GRANT USAGE ON SCHEMA COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC",
            account_statements,
        )
        self.assertNotIn(
            "GRANT SELECT ON ALL TABLES IN SCHEMA\n    COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC",
            account_statements,
        )
        self.assertIn("USE ROLE COVID_PROJECT_ADMIN", project_statements)
        self.assertIn(
            "CREATE SCHEMA IF NOT EXISTS COVID_ANALYTICS.RAW", project_statements
        )

        events: list[tuple[str, object]] = []
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = ("BOOTCAMP_USER",)

        def record_sql_file(_connection, path: Path) -> None:
            events.append(("file", path.name))

        def record_statement(query: str, parameters=()) -> None:
            events.append((query, parameters))

        cursor.execute.side_effect = record_statement
        with patch("scripts.bootstrap.execute_sql_file", side_effect=record_sql_file):
            bootstrap.bootstrap_account_objects_and_roles(connection)

        self.assertEqual(events[0], ("file", account_sql.name))
        grant_events = [event for event in events if event[0].startswith("GRANT ROLE")]
        self.assertEqual(
            [event[0] for event in grant_events],
            [
                "GRANT ROLE COVID_PROJECT_ADMIN TO USER IDENTIFIER(%s)",
                "GRANT ROLE COVID_APP_ROLE TO USER IDENTIFIER(%s)",
            ],
        )
        self.assertTrue(all(event[1] == ("BOOTCAMP_USER",) for event in grant_events))
        cursor.close.assert_called_once()

    def test_account_setup_adopts_existing_jhu_extension_objects(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            ("ACCOUNTADMIN",),
            ("COVID_PROJECT_ADMIN",),
            None,
            None,
            None,
        ]

        bootstrap._transfer_existing_jhu_extension_ownership(cursor)

        ownership_statements = [
            call.args[0]
            for call in cursor.execute.call_args_list
            if call.args[0].startswith("GRANT OWNERSHIP")
        ]
        self.assertEqual(
            ownership_statements,
            [
                "GRANT OWNERSHIP ON TABLE "
                "COVID_ANALYTICS.RAW.JHU_GEOGRAPHY_POLICY "
                "TO ROLE COVID_PROJECT_ADMIN COPY CURRENT GRANTS"
            ],
        )

    def test_current_user_role_check_supports_legacy_and_current_show_layouts(
        self,
    ) -> None:
        layouts = (
            (
                ("created_on", "role", "granted_to", "grantee_name", "granted_by"),
                (
                    (None, "COVID_PROJECT_ADMIN", "USER", "BOOTCAMP_USER", None),
                    (None, "COVID_APP_ROLE", "USER", "BOOTCAMP_USER", None),
                ),
            ),
            (
                (
                    "created_on",
                    "privilege",
                    "granted_on",
                    "name",
                    "role",
                    "granted_to",
                    "grantee_name",
                    "grant_option",
                    "granted_by",
                ),
                (
                    (
                        None,
                        None,
                        None,
                        None,
                        "COVID_PROJECT_ADMIN",
                        "USER",
                        "BOOTCAMP_USER",
                        False,
                        None,
                    ),
                    (
                        None,
                        None,
                        None,
                        None,
                        "COVID_APP_ROLE",
                        "USER",
                        "BOOTCAMP_USER",
                        False,
                        None,
                    ),
                ),
            ),
        )

        for column_names, grants in layouts:
            with self.subTest(column_names=column_names):
                connection = MagicMock()
                cursor = connection.cursor.return_value
                cursor.fetchone.return_value = ("BOOTCAMP_USER",)
                cursor.description = [(name,) for name in column_names]
                cursor.fetchall.return_value = grants

                self.assertTrue(
                    bootstrap._current_user_has_roles(
                        connection,
                        {"COVID_PROJECT_ADMIN", "COVID_APP_ROLE"},
                    )
                )
                cursor.close.assert_called_once()

    def test_current_user_role_check_reports_missing_expected_role(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = ("BOOTCAMP_USER",)
        cursor.description = [("created_on",), ("role",)]
        cursor.fetchall.return_value = ((None, "COVID_PROJECT_ADMIN"),)

        self.assertFalse(
            bootstrap._current_user_has_roles(
                connection,
                {"COVID_PROJECT_ADMIN", "COVID_APP_ROLE"},
            )
        )
        cursor.close.assert_called_once()

    def test_compose_port_owners_uses_project_labels(self) -> None:
        output = "\n".join(
            [
                json.dumps({"Ports": "0.0.0.0:8000->8000/tcp"}),
                json.dumps({"Ports": "127.0.0.1:27017->27017/tcp"}),
            ]
        )
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
        with patch("scripts.bootstrap._run_command", return_value=result) as command:
            self.assertEqual(bootstrap._compose_port_owners(), {8000, 27017})

        invoked = command.call_args.args[0]
        self.assertIn("label=com.docker.compose.project=covid-platform", invoked)

    def test_local_commands_pin_the_compose_project_name(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("scripts.bootstrap.subprocess.run", return_value=completed) as run:
            bootstrap._run_command(["docker", "compose", "version"])

        self.assertEqual(
            run.call_args.kwargs["env"]["COMPOSE_PROJECT_NAME"], "covid-platform"
        )

    def test_local_doctor_distinguishes_stopped_docker_engine(self) -> None:
        unavailable = subprocess.CompletedProcess([], 1, stdout="", stderr="redacted")
        with (
            patch("scripts.bootstrap.shutil.which", return_value="installed"),
            patch("scripts.bootstrap._run_command", return_value=unavailable),
        ):
            with self.assertRaises(bootstrap.BootstrapError) as raised:
                bootstrap.doctor_local()

        self.assertIn("engine is not responding", str(raised.exception))
        self.assertIn("Docker Desktop", raised.exception.fixes[0])
        self.assertNotIn("redacted", str(raised.exception))

    def test_configured_doctor_validates_marketplace_table(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            ("BOOTCAMP_USER", "ACCOUNTADMIN"),
            ("COVID19_EPIDEMIOLOGICAL_DATA",),
            (1,),
            (1,),
        ]
        values = {
            "SNOWFLAKE_BOOTSTRAP_ROLE": "ACCOUNTADMIN",
        }
        with patch("scripts.bootstrap.connect_snowflake", return_value=connection):
            bootstrap.doctor_configured(values)

        executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(
            any(
                "COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL" in statement
                for statement in executed_sql
            )
        )
        self.assertTrue(
            any(
                "COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.JHU_COVID_19_TIMESERIES"
                in statement
                for statement in executed_sql
            )
        )
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_api_failure_uses_stable_code_and_request_id(self) -> None:
        failure = bootstrap._api_failure(
            "http://localhost:8000/health/snowflake",
            503,
            {
                "error": {
                    "code": "snowflake_role_unauthorized",
                    "request_id": "request-123",
                }
            },
        )

        self.assertIn("COVID_APP_ROLE", failure.likely_cause)
        self.assertIn("request-123", failure.technical_reference or "")
        self.assertNotIn("password", str(failure).lower())

    def test_failure_banner_contains_actionable_sections(self) -> None:
        messages: list[str] = []
        failure = bootstrap.BootstrapError(
            "Docker is unavailable.",
            likely_cause="The engine is stopped.",
            fixes=("Start Docker Desktop.",),
            retry="docker info",
            technical_reference="audit.jsonl",
        )
        with patch(
            "scripts.bootstrap.console",
            side_effect=lambda message, **_: messages.append(message),
        ):
            bootstrap._show_failure(
                failure,
                audit_path=Path("fallback.jsonl"),
                default_retry="setup --resume",
            )

        output = "\n".join(messages)
        self.assertIn("SETUP COULD NOT CONTINUE", output)
        self.assertIn("What happened:", output)
        self.assertIn("Likely cause:", output)
        self.assertIn("How to fix:", output)
        self.assertIn("Then run: docker info", output)
        self.assertIn("Technical reference: audit.jsonl", output)

    def test_host_commands_can_be_supplied_to_containerized_bootstrap(self) -> None:
        with patch.dict(
            "scripts.bootstrap.os.environ",
            {
                "SETUP_LAUNCH_COMMAND": ".\\setup.ps1",
                "SETUP_RESUME_COMMAND": ".\\setup.ps1 --resume",
                "SETUP_START_COMMAND": ".\\start.ps1",
                "SETUP_STOP_COMMAND": ".\\stop.ps1",
            },
            clear=False,
        ):
            self.assertEqual(bootstrap._setup_command(), ".\\setup.ps1")
            self.assertEqual(
                bootstrap._setup_command(resume=True),
                ".\\setup.ps1 --resume",
            )
            self.assertEqual(bootstrap._start_command(), ".\\start.ps1")
            self.assertEqual(bootstrap._stop_command(), ".\\stop.ps1")

    def test_container_finalizer_uses_internal_compose_addresses(self) -> None:
        values = {
            "SNOWFLAKE_ACCOUNT": "organization-account",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_DATABASE": "COVID_ANALYTICS",
            "MONGODB_URI": "mongodb://mongo/covid_app",
            "MONGO_DATABASE": "covid_app",
        }
        state = MagicMock()
        with (
            patch("scripts.bootstrap.load_runtime_environment", return_value=values),
            patch("scripts.bootstrap.SetupState", return_value=state),
            patch("scripts.bootstrap.poll_http_postconditions") as poll,
            patch("scripts.bootstrap._http_postconditions_pass", return_value=True),
            patch("scripts.bootstrap._mongodb_indexes_ready", return_value=True),
            patch("scripts.bootstrap._show_setup_success"),
            patch("scripts.bootstrap.console"),
        ):
            bootstrap.finalize_container_setup(
                resume=False,
                audit_path=Path("audit.jsonl"),
            )

        self.assertTrue(poll.called)
        for call in poll.call_args_list:
            self.assertEqual(
                call.kwargs["api_base_url"],
                "http://127.0.0.1:8000",
            )
            self.assertEqual(
                call.kwargs["dashboard_base_url"],
                "http://dashboard:8050",
            )

    def test_non_interactive_configuration_lists_missing_values(self) -> None:
        root = self._temporary_root()
        example = root / ".env.example"
        configured = root / ".env"
        example.write_text(
            "\n".join(f"{key}=" for key in bootstrap.REQUIRED_ENVIRONMENT),
            encoding="utf-8",
        )
        configured.write_text("SNOWFLAKE_USER=user\n", encoding="utf-8")
        with (
            patch.object(bootstrap, "ENV_EXAMPLE_PATH", example),
            patch.object(bootstrap, "ENV_PATH", configured),
        ):
            with self.assertRaisesRegex(
                bootstrap.BootstrapError,
                "SNOWFLAKE_ACCOUNT",
            ):
                bootstrap.configure_environment(non_interactive=True)

    def test_reporting_snapshot_uses_atomic_replacement(self) -> None:
        sql = bootstrap.REPOSITORY_ROOT / "sql/06_create_reporting_objects.sql"
        text = sql.read_text(encoding="utf-8").upper()

        self.assertIn("CREATE OR REPLACE TRANSIENT TABLE", text)
        self.assertIn("COUNTRY_CONTEXT_ANALYSIS", text)
        self.assertNotIn(
            "DELETE FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS", text
        )

    def test_jhu_extension_is_parallel_and_policy_driven(self) -> None:
        sql = bootstrap.SQL_FILES["jhu_extension"].read_text(encoding="utf-8")
        normalized = " ".join(sql.upper().split())

        self.assertIn("JHU_START_DATE DATE", normalized)
        self.assertIn("DATE '2020-12-14'", normalized)
        self.assertIn(
            "COALESCE(POLICY.JHU_START_DATE, DATE '2020-12-15')",
            normalized,
        )
        self.assertIn("THEN JHU.FIRST_JHU_DATE", normalized)
        self.assertIn("COVID_COUNTRY_DAILY_EXTENDED", normalized)
        self.assertIn("COVID_ENRICHED_EXTENDED", normalized)
        self.assertIn("COVID_ENRICHED_EXTENDED_DATA", normalized)
        self.assertIn("COUNTRY_LATEST_METRICS_EXTENDED", normalized)
        self.assertIn("CASE_INCREASE_PATTERNS_EXTENDED", normalized)
        self.assertIn("CASE_INCREASE_PATTERNS_EXTENDED_DATA", normalized)
        self.assertIn(
            "CREATE OR REPLACE TRANSIENT TABLE "
            "COVID_ANALYTICS.MARTS_BUILD.COVID_ENRICHED_EXTENDED_DATA",
            normalized,
        )
        self.assertIn(
            "CREATE OR REPLACE TRANSIENT TABLE "
            "COVID_ANALYTICS.MARTS_BUILD.CASE_INCREASE_PATTERNS_EXTENDED_DATA",
            normalized,
        )
        self.assertIn(
            "CREATE OR REPLACE SCHEMA COVID_ANALYTICS.MARTS_BUILD CLONE "
            "COVID_ANALYTICS.MARTS",
            normalized,
        )
        self.assertIn("EXTENDED_PUBLICATION_STATE", normalized)
        self.assertNotIn(
            "CREATE OR REPLACE TRANSIENT TABLE "
            "COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED_DATA",
            normalized,
        )
        self.assertNotIn("ALTER SCHEMA COVID_ANALYTICS.MARTS SWAP", normalized)
        self.assertIn(
            "PARTITION BY LOCATION_KEY, COUNTRY, COUNTRY_ISO2, COUNTRY_ISO3, "
            "SERIES_SEGMENT",
            normalized,
        )
        self.assertIn("PATTERN (START_DAY INCREASE_DAY{3,})", normalized)
        self.assertIn(
            "DATEDIFF('DAY', LAG(REPORT_DATE), REPORT_DATE) = 1",
            normalized,
        )
        self.assertIn("'ECDC_BASELINE'", normalized)
        self.assertIn("'JHU_CONTINUATION'", normalized)
        self.assertIn("'JHU_ONLY'", normalized)
        self.assertNotIn(
            "CREATE OR REPLACE VIEW COVID_ANALYTICS.MARTS.COVID_ENRICHED AS",
            normalized,
        )

    def test_jhu_publication_validates_before_and_after_atomic_swap(self) -> None:
        connection = MagicMock()
        events: list[str] = []

        with (
            patch(
                "scripts.bootstrap.execute_sql_file",
                side_effect=lambda *_: events.append("build"),
            ),
            patch(
                "scripts.bootstrap._jhu_extension_ready",
                side_effect=lambda _, schema: events.append(f"validate:{schema}")
                or True,
            ),
            patch(
                "scripts.bootstrap._execute",
                side_effect=lambda *_: events.append("swap"),
            ),
        ):
            bootstrap.publish_jhu_extension(connection)

        self.assertEqual(
            events,
            ["build", "validate:MARTS_BUILD", "swap", "validate:MARTS"],
        )

    def test_jhu_publication_does_not_swap_invalid_build(self) -> None:
        connection = MagicMock()
        with (
            patch("scripts.bootstrap.execute_sql_file"),
            patch("scripts.bootstrap._jhu_extension_ready", return_value=False),
            patch("scripts.bootstrap._execute") as execute,
        ):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "build failed"):
                bootstrap.publish_jhu_extension(connection)

        execute.assert_not_called()

    def test_jhu_publication_restores_previous_generation(self) -> None:
        connection = MagicMock()
        with (
            patch("scripts.bootstrap.execute_sql_file"),
            patch(
                "scripts.bootstrap._jhu_extension_ready",
                side_effect=[True, False],
            ),
            patch("scripts.bootstrap._execute") as execute,
        ):
            with self.assertRaisesRegex(
                bootstrap.BootstrapError,
                "failed active validation",
            ):
                bootstrap.publish_jhu_extension(connection)

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(execute.call_args_list[0], execute.call_args_list[1])

    def test_analysis_and_eda_use_dataset_selected_extended_objects(self) -> None:
        analysis_sql = (
            bootstrap.REPOSITORY_ROOT / "sql/07_analysis_queries.sql"
        ).read_text(encoding="utf-8")
        eda_script = (bootstrap.REPOSITORY_ROOT / "scripts/run_eda.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("COUNTRY_LATEST_METRICS_EXTENDED", analysis_sql)
        self.assertIn("CASE_INCREASE_PATTERNS_EXTENDED", analysis_sql)
        self.assertIn("COVID_DATASET_OBJECTS", eda_script)
        self.assertIn("sys.path.insert(0, str(REPOSITORY_ROOT))", eda_script)
        self.assertIn("dataset_objects.patterns", eda_script)
        self.assertIn('"case_increase_patterns"', eda_script)


if __name__ == "__main__":
    unittest.main()
