from __future__ import annotations

import io
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
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

    def test_reporting_snapshot_uses_transactional_refresh(self) -> None:
        sql = bootstrap.REPOSITORY_ROOT / "sql/06_create_reporting_objects.sql"
        text = sql.read_text(encoding="utf-8").upper()

        self.assertNotIn("CREATE OR REPLACE TRANSIENT TABLE", text)
        self.assertIn("CREATE TRANSIENT TABLE IF NOT EXISTS", text)
        self.assertIn("BEGIN TRANSACTION;", text)
        self.assertIn("DELETE FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS", text)
        self.assertIn("COMMIT;", text)


if __name__ == "__main__":
    unittest.main()
