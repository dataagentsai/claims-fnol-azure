"""The CI and deploy workflows (Tier 4a A10), read offline.

Nothing here runs a workflow or calls GitHub or Azure. What is held:

1. Both workflows parse, and every script a step runs exists and is executable.
2. The gates run in CI, after the suite and before anything is pushed; deploy
   follows only a successful CI.
3. Images are pushed only from a push to main.
4. Deploy signs in with OIDC: an id-token permission, no client secret anywhere.
5. The sibling checkouts are where the code, the gates and the Dockerfiles
   expect them (`../<repo>`), and each image is built with the contexts its
   Dockerfile names.
6. The PostgreSQL service gives the tests the variables they read.
7. The setup script lets the deploy identity assign exactly the roles the Bicep
   assigns.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI_TEXT = (WORKFLOWS / "ci.yml").read_text()
DEPLOY_TEXT = (WORKFLOWS / "deploy.yml").read_text()
CI: dict[Any, Any] = yaml.safe_load(CI_TEXT)
DEPLOY: dict[Any, Any] = yaml.safe_load(DEPLOY_TEXT)
SETUP = (ROOT / "scripts" / "ci" / "setup-federated-credential.sh").read_text()


def triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the bare key `on` as True.
    found = workflow.get("on", workflow.get(True))
    assert isinstance(found, dict)
    return found


def steps(workflow: dict[Any, Any], job: str) -> list[dict[str, Any]]:
    return list(workflow["jobs"][job]["steps"])


def step(workflow: dict[Any, Any], job: str, name: str) -> tuple[int, dict[str, Any]]:
    for i, s in enumerate(steps(workflow, job)):
        if s.get("name") == name:
            return i, s
    raise AssertionError(f"{job} has no step named {name!r}")


def checkouts(workflow: dict[Any, Any], job: str) -> dict[str, str]:
    """path -> repository ('' for this one) of each actions/checkout step."""
    found = {}
    for s in steps(workflow, job):
        if str(s.get("uses", "")).startswith("actions/checkout@"):
            w = s.get("with") or {}
            found[w.get("path", ".")] = w.get("repository", "")
    return found


# --------------------------------------------------------- 1 parse; scripts exist
SCRIPT = re.compile(r"(?<![\w/.-])((?:scripts|infra/hooks)/[\w./-]+\.(?:sh|py))")


def scripts_run(workflow: dict[Any, Any]) -> set[str]:
    found: set[str] = set()
    for job in workflow["jobs"].values():
        for s in job.get("steps", []):
            found |= set(SCRIPT.findall(str(s.get("run", ""))))
    return found


@pytest.mark.parametrize(
    ("name", "workflow", "jobs"),
    [
        ("ci.yml", CI, {"checks", "images"}),
        ("deploy.yml", DEPLOY, {"ready", "deploy"}),
    ],
)
def test_each_workflow_parses_with_its_jobs(
    name: str, workflow: dict[Any, Any], jobs: set[str]
) -> None:
    assert workflow["name"]
    assert triggers(workflow)
    assert set(workflow["jobs"]) == jobs, name
    for job, body in workflow["jobs"].items():
        assert body.get("timeout-minutes"), f"{name}:{job} has no timeout"
        assert body.get("runs-on"), f"{name}:{job}"


SCRIPTS = sorted(
    {(n, s) for n, w in (("ci.yml", CI), ("deploy.yml", DEPLOY)) for s in scripts_run(w)}
)


def test_the_workflows_run_scripts_at_all() -> None:
    assert {s for _, s in SCRIPTS} >= {
        "scripts/ci/siblings.sh",
        "scripts/ci/actionlint.sh",
        "scripts/ci/deploy-ready.sh",
        "scripts/ci/deploy-env.sh",
        "scripts/ci/deploy-preflight.sh",
        "scripts/ci/db-firewall.sh",
        "infra/hooks/preprovision.sh",
        "infra/hooks/db-roles.sh",
    }


@pytest.mark.parametrize(("workflow", "script"), SCRIPTS, ids=[f"{w}:{s}" for w, s in SCRIPTS])
def test_every_script_a_step_runs_exists_and_is_executable(workflow: str, script: str) -> None:
    path = ROOT / script
    assert path.is_file(), f"{workflow} runs {script}, which does not exist"
    assert os.access(path, os.X_OK), f"{script} is not executable (git update-index --chmod=+x)"


# ------------------------------------------------- 2 gates first; deploy after CI
@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        ("ruff", "The full suite"),
        ("mypy", "The full suite"),
        ("lint-imports", "The full suite"),
        ("The full suite", "The four gates"),
        ("The four gates", "Upload the gates' verdict"),
    ],
)
def test_the_checks_run_in_order(earlier: str, later: str) -> None:
    assert step(CI, "checks", earlier)[0] < step(CI, "checks", later)[0]


def test_the_gates_step_runs_the_gates_tool_on_this_repo_reusing_the_suite() -> None:
    _, gates = step(CI, "checks", "The four gates")
    assert gates["working-directory"] == "clean-ai-engineering"
    assert gates["run"].strip() == "uv run tools/gates.py ../claims-fnol-azure --skip-tests"
    _, suite = step(CI, "checks", "The full suite")
    gates_yaml = yaml.safe_load((ROOT / "gates.yaml").read_text())
    expected = gates_yaml["tests"]["command"].format(junit="reports/gates/junit.xml")
    assert suite["run"].strip() == expected, "the suite must run as the gates would run it"
    _, upload = step(CI, "checks", "Upload the gates' verdict")
    assert upload["if"] == "always()"
    assert "claims-fnol-azure/reports/gates/verdict.md" in upload["with"]["path"]


def test_nothing_is_pushed_before_the_gates_pass() -> None:
    # Pushing happens only in `images`, which needs `checks` (where the gates are).
    for job, body in CI["jobs"].items():
        pushes = any("push" in (s.get("with") or {}) for s in body["steps"])
        if pushes:
            assert "checks" in (
                [body["needs"]] if isinstance(body.get("needs"), str) else body.get("needs", [])
            ), job
    assert not any("push" in (s.get("with") or {}) for s in steps(CI, "checks"))


def test_deploy_follows_only_a_successful_ci_run() -> None:
    on = triggers(DEPLOY)
    assert set(on) == {"workflow_dispatch", "workflow_run"}
    assert on["workflow_run"]["workflows"] == [CI["name"]]
    assert on["workflow_run"]["types"] == ["completed"]
    assert on["workflow_run"]["branches"] == ["main"]
    deploy = DEPLOY["jobs"]["deploy"]
    assert deploy["needs"] == "ready"
    assert deploy["if"] == "needs.ready.outputs.ready == 'true'"
    ready = (ROOT / "scripts" / "ci" / "deploy-ready.sh").read_text()
    for check in ('RUN_CONCLUSION:-}" == "success"', 'RUN_EVENT:-}" == "push"', "status=success"):
        assert check in ready
    assert DEPLOY["concurrency"]["cancel-in-progress"] is False


# ------------------------------------------------------------ 3 push only on main
def test_ci_runs_on_push_and_pull_request_to_main() -> None:
    on = triggers(CI)
    assert on["push"]["branches"] == ["main"]
    assert on["pull_request"]["branches"] == ["main"]


@pytest.mark.parametrize("image", ["Build the agent image", "Build the claims-system image"])
def test_images_push_only_from_a_push_to_main(image: str) -> None:
    images = CI["jobs"]["images"]
    assert images["env"]["PUSH"] == (
        "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}"
    )
    assert images["permissions"] == {"contents": "read", "packages": "write"}
    assert CI["permissions"] == {"contents": "read"}, "only the images job may write packages"
    _, build = step(CI, "images", image)
    assert build["with"]["push"] == "${{ env.PUSH == 'true' }}"
    tags = build["with"]["tags"].strip().splitlines()
    assert len(tags) == 2
    assert tags[0].endswith(":${{ github.sha }}") and tags[1].endswith(":latest")
    _, login = step(CI, "images", "Log in to GitHub Container Registry")
    assert login["if"] == "env.PUSH == 'true'"
    assert login["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"


@pytest.mark.parametrize(
    ("variable", "image"),
    [
        ("AGENT_IMAGE", "ghcr.io/dataagentsai/claims-fnol-agent"),
        ("CLAIMS_SYSTEM_IMAGE", "ghcr.io/dataagentsai/claims-fnol-claims-system"),
    ],
)
def test_ci_pushes_the_images_deploy_runs(variable: str, image: str) -> None:
    assert CI["env"][variable] == image
    deploy_env = (ROOT / "scripts" / "ci" / "deploy-env.sh").read_text()
    name = image.rsplit("/", 1)[1]
    assert f'set_value {variable} "$registry/{name}:$IMAGE_TAG"' in deploy_env
    assert 'registry="${IMAGE_REGISTRY:-ghcr.io/dataagentsai}"' in deploy_env


# -------------------------------------------------------- 4 OIDC, never a secret
def test_deploy_signs_in_with_oidc() -> None:
    deploy = DEPLOY["jobs"]["deploy"]
    assert deploy["permissions"]["id-token"] == "write"
    assert deploy["environment"] == "dev"
    login = next(s for s in deploy["steps"] if str(s.get("uses", "")).startswith("azure/login@"))
    assert set(login["with"]) == {"client-id", "tenant-id", "subscription-id"}
    azd = step(DEPLOY, "deploy", "azd sign-in (the same federated credential)")[1]["run"]
    assert "--federated-credential-provider github" in azd


@pytest.mark.parametrize(
    "forbidden",
    ["client-secret", "AZURE_CLIENT_SECRET", "creds:", "AZURE_CREDENTIALS", "--client-secret"],
)
def test_no_client_secret_anywhere(forbidden: str) -> None:
    assert forbidden not in DEPLOY_TEXT
    assert forbidden not in CI_TEXT
    assert forbidden not in SETUP.replace("no client secret", "")


@pytest.mark.parametrize(
    "subject",
    [
        "repo:$REPO:ref:refs/heads/main",
        "repo:$REPO:environment:dev",
    ],
)
def test_the_setup_script_trusts_main_and_the_dev_environment(subject: str) -> None:
    assert 'REPO="${GITHUB_REPO:-dataagentsai/claims-fnol-azure}"' in SETUP
    assert f'"{subject}"' in SETUP
    assert "api://AzureADTokenExchange" in SETUP
    assert "--yes" in SETUP and "confirm " in SETUP


def test_deploy_runs_the_database_roles_through_a_firewall_rule_it_removes() -> None:
    i_open, _ = step(DEPLOY, "deploy", "Admit this runner to PostgreSQL")
    i_roles, roles = step(
        DEPLOY, "deploy", "Database roles (infra/hooks/db-roles.sh, as the Mac runs it)"
    )
    i_close, close = step(DEPLOY, "deploy", "Remove this runner from PostgreSQL's firewall")
    assert i_open < i_roles < i_close
    assert close["if"].startswith("always()")
    assert "infra/hooks/db-roles.sh" in roles["run"]
    i_prov, _ = step(DEPLOY, "deploy", "azd provision")
    i_flags, _ = step(DEPLOY, "deploy", "Preflight (provision keeps the real secrets)")
    i_strip, _ = step(DEPLOY, "deploy", "The runner's azure.yaml without the Mac's hooks")
    assert i_strip < i_flags < i_prov < i_open


# ------------------------------------------------------- 5 siblings where expected
def siblings_named() -> set[str]:
    """Every `../<repo>` the code, the gates and the build read."""
    sources = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
    found = {Path(s["path"]).parts[1] for s in sources.values() if "path" in s}
    gates = (ROOT / "gates.yaml").read_text()
    found |= set(re.findall(r"\.\./([\w-]+)/", gates))
    # tools/gates.py reads the two catalogs beside clean-ai-engineering (AHC, AAC).
    found |= {"ai-harness-catalog", "ai-assurance-catalog"}
    return found


def test_the_checks_job_checks_out_every_sibling_beside_this_repo() -> None:
    paths = checkouts(CI, "checks")
    assert paths.pop("claims-fnol-azure") == ""
    assert set(paths) == siblings_named()
    for path, repository in paths.items():
        assert repository == f"dataagentsai/{path}"
    assert CI["jobs"]["checks"]["defaults"]["run"]["working-directory"] == "claims-fnol-azure"


def dockerfile_contexts(dockerfile: str) -> dict[str, str]:
    text = (ROOT / dockerfile).read_text()
    return dict(re.findall(r"--build-context (\w+)=\.\./(\S+)", text))


@pytest.mark.parametrize(
    ("dockerfile", "step_name"),
    [
        ("agent.Dockerfile", "Build the agent image"),
        ("claims-system.Dockerfile", "Build the claims-system image"),
    ],
)
def test_each_image_is_built_with_the_contexts_its_dockerfile_names(
    dockerfile: str, step_name: str
) -> None:
    expected = dockerfile_contexts(dockerfile)
    assert expected, f"{dockerfile} names no build context"
    _, build = step(CI, "images", step_name)
    given = dict(line.split("=", 1) for line in build["with"]["build-contexts"].split())
    assert given == expected
    assert build["with"]["context"] == "claims-fnol-azure"
    assert build["with"]["file"] == f"claims-fnol-azure/{dockerfile}"
    checked_out = set(checkouts(CI, "images"))
    for path in given.values():
        assert path.split("/", 1)[0] in checked_out, f"{path} is not checked out in images"


def test_the_sibling_refs_are_named_once_and_pinnable() -> None:
    refs = {k: v for k, v in CI["env"].items() if k.endswith("_REF")}
    assert len(refs) == len(siblings_named())
    assert "To pin one, set" in CI_TEXT


# ----------------------------------------------------- 6 PostgreSQL for the tests
def variables_the_tests_read() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "tests").glob("*.py"):
        found |= set(re.findall(r"os\.environ(?:\.get\(|\[)\"(\w+)\"", path.read_text()))
    return found


def test_the_postgres_service_gives_the_tests_what_they_read() -> None:
    checks = CI["jobs"]["checks"]
    read = variables_the_tests_read()
    assert "CLAIMS_TEST_SERVER_URL" in read
    for name in read:
        assert name in checks["env"], f"the tests read {name}; the checks job does not set it"
    url = urlsplit(checks["env"]["CLAIMS_TEST_SERVER_URL"])
    service = checks["services"]["postgres"]
    assert service["image"].startswith("postgres:16")
    assert url.username == service["env"]["POSTGRES_USER"] == "postgres", (
        "the superuser: CREATEROLE"
    )
    assert url.password == service["env"]["POSTGRES_PASSWORD"]
    assert f"{url.port}:5432" in service["ports"]
    assert url.path in ("", "/"), "no database in the URL (tests/pg.py)"
    # An address, not a name: a name is resolved on the loop's default executor,
    # which DBOS has shut down by the role teardown (FINDINGS F-103).
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", url.hostname or ""), url.hostname


# ------------------------------------------- 7 the roles the deploy may assign
def test_the_deploy_identity_may_assign_exactly_the_bicep_roles() -> None:
    bicep = "\n".join(p.read_text() for p in (ROOT / "infra").rglob("*.bicep"))
    assigned = set(re.findall(r"var \w+ = '([0-9a-f-]{36})'", bicep))
    allowed = set(re.findall(r"^\s+([0-9a-f-]{36}) #", SETUP, flags=re.M))
    assert assigned == allowed


def test_actionlint_passes_when_installed() -> None:
    if shutil.which("actionlint") is None:
        pytest.skip("actionlint is not installed (brew install actionlint)")
    done = subprocess.run(
        [str(ROOT / "scripts" / "ci" / "actionlint.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
