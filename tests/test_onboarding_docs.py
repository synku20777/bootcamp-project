from __future__ import annotations

import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class OnboardingDocumentationTests(unittest.TestCase):
    def test_beginner_start_path_precedes_architecture(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

        start = readme.index("## Start here: run the project from a new computer")
        architecture = readme.index("## Architecture")

        self.assertLess(start, architecture)
        self.assertIn("## Advanced: manual setup and recovery", readme)
        self.assertIn("COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL", readme)
        self.assertIn("Set-ExecutionPolicy -Scope Process Bypass", readme)
        self.assertIn("chmod +x setup.sh start.sh stop.sh", readme)

    def test_environment_example_uses_safe_connector_placeholder_and_roles(
        self,
    ) -> None:
        example = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn(
            "SNOWFLAKE_ACCOUNT=your_organization-your_account",
            example,
        )
        self.assertIn("SNOWFLAKE_BOOTSTRAP_ROLE=ACCOUNTADMIN", example)
        self.assertIn("SNOWFLAKE_ROLE=COVID_PROJECT_ADMIN", example)
        self.assertIn("SNOWFLAKE_API_ROLE=COVID_APP_ROLE", example)
        self.assertNotIn("https://", _environment_value(example, "SNOWFLAKE_ACCOUNT"))

    # def test_web_onboarding_decision_preserves_bootstrap_authority(self) -> None:
    #     decision = (
    #         REPOSITORY_ROOT / "docs" / "architecture" / "web-onboarding-feasibility.md"
    #     ).read_text(encoding="utf-8")

    #     self.assertIn("localhost-only", decision)
    #     self.assertIn("CSRF", decision)
    #     self.assertIn("one-time, short-lived setup token", decision)
    #     self.assertIn(
    #         "Keep `scripts/bootstrap.py` as the only setup authority", decision
    #     )
    #     self.assertIn("Do not\nmount the Docker socket", decision)
    #     self.assertIn("setup page inside the production dashboard", decision)

    def test_setup_wrappers_propagate_failures_with_guidance(self) -> None:
        powershell = (REPOSITORY_ROOT / "setup.ps1").read_text(encoding="utf-8")
        shell = (REPOSITORY_ROOT / "setup.sh").read_text(encoding="utf-8")

        for wrapper in (powershell, shell):
            self.assertIn("SETUP COULD NOT CONTINUE", wrapper)
            self.assertIn("Likely cause:", wrapper)
            self.assertIn("How to fix:", wrapper)
            self.assertIn("Technical reference:", wrapper)
            self.assertIn("compose.setup.yaml", wrapper)
            self.assertIn("setup-data", wrapper)
            self.assertNotIn("Get-Command uv", wrapper)
            self.assertNotIn("command -v uv", wrapper)
        self.assertIn("exit $LASTEXITCODE", powershell)

    def test_setup_compose_file_does_not_mount_the_docker_socket(self) -> None:
        setup_compose = (REPOSITORY_ROOT / "compose.setup.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("dockerfile: dockerfile", setup_compose)
        self.assertIn("scripts.bootstrap", setup_compose)
        self.assertIn("- .:/app", setup_compose)
        self.assertNotIn("docker.sock", setup_compose)

        dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn(".env\n", dockerignore)
        self.assertIn(".env.backup-*", dockerignore)
        self.assertIn(".setup-state.json", dockerignore)

    def test_normal_user_path_explicitly_requires_no_local_python_or_uv(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        primary_path = readme.split("## Current capabilities", maxsplit=1)[0]

        self.assertIn("requires **Docker only**", primary_path)
        self.assertIn("do not need to install Python, uv", primary_path)
        self.assertIn("private Python/uv environment", primary_path)


def _environment_value(content: str, key: str) -> str:
    prefix = f"{key}="
    return next(
        line.removeprefix(prefix)
        for line in content.splitlines()
        if line.startswith(prefix)
    )


if __name__ == "__main__":
    unittest.main()
