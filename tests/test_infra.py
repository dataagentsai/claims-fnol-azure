"""Tier 3 — the Azure infrastructure, checked with no Azure tools and no network.

`az`, `azd` and `bicep` are not installed on this Mac, so nothing here compiles
Bicep or talks to Azure. What can be checked by reading is checked:

1. azure.yaml: two containerapp services deployed from an image (no build, no
   registry of ours), the Bicep entry point and the hooks it names exist.
2. main.parameters.json names only parameters main.bicep declares, and every
   parameter main.bicep needs without a default is given.
3. Every module main.bicep uses exists, starts with the three plain-English
   sections, receives only parameters it declares and every one it requires,
   and every `<module>.outputs.<name>` main.bicep reads is an output of it.
4. One API version per resource type across all the Bicep.
5. The overlay (config/azure.yaml) against the infra: each {env: NAME} is a
   main.bicep output set under the same name in the agent's environment; each
   {key_vault: name} is a secret some module writes.
6. APIM's policy: under Consumption's 16 KiB, every {{named value}} created by
   apim.bicep, every section present.
7. The Dockerfiles: what they copy exists, the sibling checkouts they name
   exist where the build command says, the CMD's module exists, and .env is
   never sent to a build.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
MAIN = (INFRA / "main.bicep").read_text()

PARAM = re.compile(r"^param\s+(\w+)\s+\w+(\s*=.*)?$", re.M)
OUTPUT = re.compile(r"^output\s+(\w+)\s+\w+\s*=", re.M)
MODULE = re.compile(r"^module\s+(\w+)\s+'([^']+)'\s*=\s*\{", re.M)
OUTPUT_READ = re.compile(r"\b(\w+)\.outputs\.(\w+)")
API = re.compile(r"'(Microsoft\.[\w./]+)@([\w.-]+)'")
SECRET_WRITE = re.compile(
    r"'Microsoft\.KeyVault/vaults/secrets@[\w-]+'\s*=\s*(?:if\s*\([^)]*\)\s*)?\{\s*"
    r"parent:\s*\w+\s*name:\s*'([^']+)'"
)
HEADER = ("// WHAT IT IS", "// WHICH CONCERN IT SERVES", "// EXPECTED DEV COST")


def params(bicep: str) -> dict[str, bool]:
    """Declared parameters -> whether each has a default."""
    return {m.group(1): bool(m.group(2)) for m in PARAM.finditer(bicep)}


def block(text: str, start: int) -> str:
    """The brace-balanced block opening at or after `start`."""
    opened = text.index("{", start)
    depth = 0
    for i in range(opened, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[opened : i + 1]
    raise AssertionError("unbalanced braces")


def given(module_body: str) -> set[str]:
    """The parameter names a `module` statement passes."""
    inner = block(module_body, module_body.index("params:"))[1:-1]
    depth, names = 0, set()
    for line in inner.splitlines():
        if depth == 0 and (m := re.match(r"\s*(\w+):", line)):
            names.add(m.group(1))
        depth += line.count("{") + line.count("[") - line.count("}") - line.count("]")
    return names


MODULES = {m.group(1): (m.group(2), block(MAIN, m.start())) for m in MODULE.finditer(MAIN)}
ALL_BICEP = {p: p.read_text() for p in INFRA.rglob("*.bicep")}


# ------------------------------------------------------------------- 1 azure.yaml
def test_azure_yaml_deploys_two_prebuilt_images_through_the_bicep_and_hooks() -> None:
    project = yaml.safe_load((ROOT / "azure.yaml").read_text())
    infra = project["infra"]
    assert (ROOT / infra["path"] / f"{infra['module']}.bicep").is_file()
    for service in ("agent", "claims-system"):
        entry = project["services"][service]
        assert entry["host"] == "containerapp"
        assert "project" not in entry, "an image, not a build: no registry of ours (azd schema)"
        assert re.fullmatch(r"\$\{[A-Z_]+\}", entry["image"])
        assert f"'{service}'" in MAIN, f"main.bicep deploys no app tagged {service}"
    for hook in project["hooks"].values():
        script = ROOT / hook["run"]
        assert script.is_file() and os.access(script, os.X_OK), script


# --------------------------------------------------------------- 2 the parameters
def test_the_parameters_file_matches_main_bicep() -> None:
    declared = params(MAIN)
    supplied = json.loads((INFRA / "main.parameters.json").read_text())["parameters"]
    assert set(supplied) <= set(declared), set(supplied) - set(declared)
    needed = {name for name, has_default in declared.items() if not has_default}
    assert needed <= set(supplied), needed - set(supplied)


# ------------------------------------------------------------------- 3 the modules
@pytest.mark.parametrize("module", sorted(MODULES))
def test_each_module_exists_is_explained_and_is_wired_to_its_parameters(module: str) -> None:
    path, body = MODULES[module]
    source = (INFRA / path).read_text()
    for section in HEADER:
        assert section in source.split("targetScope")[0], f"{path} lacks {section!r}"
    declared = params(source)
    passed = given(body)
    assert passed <= set(declared), f"{module} passes unknown {passed - set(declared)}"
    required = {name for name, has_default in declared.items() if not has_default}
    assert required <= passed, f"{module} is missing {required - passed}"


def test_every_module_file_is_used_and_every_output_read_exists() -> None:
    used = {(INFRA / path).resolve() for path, _ in MODULES.values()}
    assert {p.resolve() for p in (INFRA / "modules").glob("*.bicep")} == used
    outputs = {
        name: set(OUTPUT.findall((INFRA / path).read_text())) for name, (path, _) in MODULES.items()
    }
    for module, output in OUTPUT_READ.findall(MAIN):
        assert output in outputs[module], f"{module}.outputs.{output} is not an output"


# --------------------------------------------------------------- 4 API versions
def test_one_api_version_per_resource_type() -> None:
    versions: dict[str, set[str]] = {}
    for text in ALL_BICEP.values():
        for kind, version in API.findall(text):
            versions.setdefault(kind, set()).add(version)
    mixed = {kind: v for kind, v in versions.items() if len(v) > 1}
    assert not mixed, mixed


# ------------------------------------------------- 5 the overlay against the infra
def references(node: object, kind: str) -> set[str]:
    if isinstance(node, dict):
        if kind in node and len(node) <= 2:
            return {str(node[kind])}
        return set().union(*(references(v, kind) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(references(v, kind) for v in node))
    return set()


OVERLAY = yaml.safe_load((ROOT / "config" / "azure.yaml").read_text())
AGENT_ENV = set(re.findall(r"\{ name: '(\w+)', value:", MODULES["agent"][1]))
MAIN_OUTPUTS = set(OUTPUT.findall(MAIN))
SECRETS_WRITTEN = set().union(*(set(SECRET_WRITE.findall(t)) for t in ALL_BICEP.values()))


@pytest.mark.parametrize("name", sorted(references(OVERLAY, "env")))
def test_each_environment_name_the_overlay_reads_is_set_by_the_deployment(name: str) -> None:
    assert name in MAIN_OUTPUTS, f"{name} is not an output of main.bicep (azd's .env)"
    assert name in AGENT_ENV, f"{name} is not in the agent container's environment"


@pytest.mark.parametrize("name", sorted(references(OVERLAY, "key_vault")))
def test_each_vault_secret_the_overlay_reads_is_written_by_a_module(name: str) -> None:
    assert name in SECRETS_WRITTEN, f"no module writes {name}"


CLAIMS_OVERLAY = yaml.safe_load((ROOT / "config" / "claims-system" / "azure.yaml").read_text())
CLAIMS_ENV = set(re.findall(r"\{ name: '(\w+)', value:", MODULES["claimsSystem"][1]))


@pytest.mark.discharges("AHC-0004")
@pytest.mark.parametrize("name", sorted(references(CLAIMS_OVERLAY, "env")))
def test_each_name_the_claims_overlay_reads_is_in_the_claims_container(name: str) -> None:
    """A1: the claims system's own overlay (config/claims-system/azure.yaml)."""
    assert name in CLAIMS_ENV, f"{name} is not in the claims container's environment"
    assert name in MAIN_OUTPUTS, f"{name} is not an output of main.bicep (azd's .env)"


@pytest.mark.discharges("AHC-0004")
def test_the_claims_container_runs_its_azure_overlay() -> None:
    assert "{ name: 'CLAIMS_SYSTEM_ENV', value: 'azure' }" in MODULES["claimsSystem"][1]
    assert not references(CLAIMS_OVERLAY, "key_vault"), "the claims overlay holds no secret"


def test_no_secret_value_or_resource_name_is_written_into_the_overlay() -> None:
    text = (ROOT / "config" / "azure.yaml").read_text()
    assert ".vault.azure.net" not in text and ".azure-api.net" not in text


# ------------------------------------------------------------- 6 APIM's policy
POLICY = (INFRA / "policies" / "groq-api.xml").read_text()
APIM = (INFRA / "modules" / "apim.bicep").read_text()


def test_the_policy_fits_consumption_and_every_named_value_exists() -> None:
    assert len(POLICY.encode()) < 16 * 1024, "Consumption: policy document 16 KiB"
    assert "loadTextContent('../policies/groq-api.xml')" in APIM
    defined = set(
        re.findall(r"namedValues@[\w-]+'\s*=\s*\{\s*parent:\s*\w+\s*name:\s*'([^']+)'", APIM)
    )
    assert set(re.findall(r"\{\{([\w-]+)\}\}", POLICY)) <= defined
    for section in ("<inbound>", "<backend>", "<outbound>", "<on-error>"):
        assert POLICY.count(section) == 1, section
    for absent in ("llm-token-limit", "rate-limit-by-key", "llm-content-safety"):
        assert f"<{absent}" not in POLICY, f"{absent} is not available on Consumption"


# ------------------------------------------------------------- 7 the Dockerfiles
# (Dockerfile, the sibling checkouts its build command names, its CMD's module)
DOCKERFILES: list[tuple[str, dict[str, str], str]] = [
    (
        "agent.Dockerfile",
        {
            "harness": "../reference-agent/packages/agent-harness",
            "stacks": "../clean-ai-engineering/stacks",
        },
        "claims_fnol_app",
    ),
    (
        "claims-system.Dockerfile",
        {
            "harness": "../reference-agent/packages/agent-harness",
            "worlds": "../clean-ai-engineering/gates/motor-claims-fnol/worlds",
        },
        "claims_system",
    ),
]


@pytest.mark.parametrize(
    ("dockerfile", "contexts", "module"), DOCKERFILES, ids=[d[0] for d in DOCKERFILES]
)
def test_each_dockerfile_copies_what_exists(
    dockerfile: str, contexts: dict[str, str], module: str
) -> None:
    text = (ROOT / dockerfile).read_text()
    for name, relative in contexts.items():
        assert f"--build-context {name}={relative}" in text
        assert f"COPY --from={name} " in text
        assert (ROOT / relative).is_dir(), relative
    for line in re.findall(r"^COPY (?!--from)(.+)$", text, re.M):
        for source in line.split()[:-1]:
            assert (ROOT / source).exists(), f"{dockerfile} copies missing {source}"
    assert (ROOT / "src" / module / "__main__.py").is_file()
    assert re.search(r"^USER \d+$", text, re.M), "runs as a non-root user"


def test_no_build_is_sent_the_local_secrets() -> None:
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert {".env", ".venv/", ".azure/"} <= set(ignored)


# ------------------------------------------------------------- Tier 4a A7, A8
@pytest.mark.discharges("AHC-0094")
@pytest.mark.parametrize(
    ("what", "pattern", "where"),
    [
        ("the agent app can run two revisions (canary)", r"revisionsMode: 'Multiple'", "main"),
        (
            "a Multiple app gives its latest revision all traffic",
            r"latestRevision: true, weight: 100",
            "app",
        ),
        ("the allow-list is a named value", r"name: 'allowed-models'", "apim"),
        ("main passes the allow-list to APIM", r"allowedModels: allowedModels", "main"),
        (
            "the policy refuses any other model with 403",
            r'"\{\{allowed-models\}\}"\.Split',
            "policy",
        ),
        ("the refusal is a 403", r'code="403" reason="Model not allowed"', "policy"),
    ],
    ids=lambda v: v if isinstance(v, str) and " " in v else "",
)
def test_canary_revisions_and_the_model_allow_list(what: str, pattern: str, where: str) -> None:
    text = {
        "main": MAIN,
        "app": (INFRA / "modules" / "containerapp.bicep").read_text(),
        "apim": APIM,
        "policy": POLICY,
    }[where]
    assert re.search(pattern, text), what


def test_the_model_check_comes_before_any_paid_call() -> None:
    inbound = POLICY[POLICY.index("<inbound>") : POLICY.index("</inbound>")]
    assert inbound.index("allowed-models") < inbound.index("content-safety-endpoint")
    assert inbound.index("allowed-models") < inbound.index("<rate-limit")


# ------------------------------------------------------- Tier 4a A4: one login per app
POSTGRES = (INFRA / "modules" / "postgres.bicep").read_text()
DB_ROLES = (INFRA / "hooks" / "db-roles.sh").read_text()
KV_SECRET_REF = re.compile(r"secret: '([\w-]+)'")
LISTED = re.compile(r"secretNames:\s*\[([^\]]*)\]")


def readable(app: str) -> set[str]:
    """Every vault secret an app's identity or container gets: its container's
    Key Vault references and its per-secret read access (keyvault-access)."""
    container, access = {
        "agent": ("agent", "agentSecrets"),
        "claims": ("claimsSystem", "claimsSecrets"),
    }[app]
    listed = LISTED.search(MODULES[access][1])
    assert listed, f"{access} lists no secrets"
    return set(KV_SECRET_REF.findall(MODULES[container][1])) | set(
        re.findall(r"'([\w-]+)'", listed.group(1))
    )


SECRET_ACCESS = [
    # (app, secret, may it read it)
    ("agent", "agent-database-url", True),
    ("agent", "claims-database-url", False),
    ("agent", "claims-records-database-url", False),
    ("agent", "postgres-admin-password", False),
    ("agent", "postgres-agent-password", False),
    ("agent", "postgres-claims-system-password", False),
    ("claims", "claims-database-url", True),
    ("claims", "claims-records-database-url", True),
    ("claims", "agent-database-url", False),
    ("claims", "postgres-admin-password", False),
    ("claims", "postgres-agent-password", False),
    ("claims", "postgres-claims-system-password", False),
]


@pytest.mark.discharges("AHC-0040")
@pytest.mark.parametrize(
    ("app", "secret", "may"),
    SECRET_ACCESS,
    ids=[f"{a} {'reads' if m else 'never reads'} {s}" for a, s, m in SECRET_ACCESS],
)
def test_each_app_reads_only_its_own_database_login(app: str, secret: str, may: bool) -> None:
    assert (secret in readable(app)) is may


@pytest.mark.discharges("AHC-0040")
def test_what_each_app_may_read_is_exactly_what_it_reads() -> None:
    agent_access = LISTED.search(MODULES["agentSecrets"][1])
    assert agent_access
    assert set(re.findall(r"'([\w-]+)'", agent_access.group(1))) == references(OVERLAY, "key_vault")
    assert not KV_SECRET_REF.findall(MODULES["agent"][1]), "the agent reads its secrets itself"
    claims_access = LISTED.search(MODULES["claimsSecrets"][1])
    assert claims_access
    assert set(re.findall(r"'([\w-]+)'", claims_access.group(1))) == set(
        KV_SECRET_REF.findall(MODULES["claimsSystem"][1])
    )
    for app in ("agentSecrets", "claimsSecrets"):
        assert MODULES[app][0] == "modules/keyvault-access.bicep"
    readers = MODULES["keyVault"][1].split("readerPrincipalIds:")[1].split("]")[0]
    assert re.findall(r"identities\.outputs\.(\w+)", readers) == ["apimPrincipalId"], (
        "no app identity may read the whole vault"
    )


DATABASE_URLS = [
    # (vault secret, the login it must name, its database)
    ("agent-database-url", "agent", "claims_fnol_dbos"),
    ("claims-database-url", "claimsSystem", "claims_fnol"),
    ("claims-records-database-url", "claimsSystem", "claims_fnol_dbos"),
]


@pytest.mark.discharges("AHC-0040")
@pytest.mark.parametrize(
    ("secret", "login", "database"), DATABASE_URLS, ids=[u[0] for u in DATABASE_URLS]
)
def test_each_database_url_names_an_app_login_never_the_administrator(
    secret: str, login: str, database: str
) -> None:
    at = re.search(rf"name: '{secret}'\s*properties:\s*\{{\s*value: '([^']+)'", POSTGRES)
    assert at, f"postgres.bicep writes no {secret}"
    assert at.group(1).startswith(f"postgresql://${{{login}}}@"), at.group(1)
    assert f":5432/{database}?sslmode=require" in at.group(1)
    assert "administrator" not in at.group(1)


ROLE_HOOK = [
    ("postprovision runs it", r'"\$here/db-roles\.sh"', "post"),
    ("it runs the same roles.sql as dev-up", r'-f "\$root/infra/sql/roles\.sql"', "hook"),
    ("the agent's login", r"-v agent_role=claims_agent", "hook"),
    ("the claims system's login", r"-v system_role=claims_system", "hook"),
    (
        "the admin password from Key Vault",
        r"PGPASSWORD=\"\$\(secret postgres-admin-password\)\"",
        "hook",
    ),
    ("the agent's password from Key Vault", r"\$\(secret postgres-agent-password\)", "hook"),
    ("the claims system's from Key Vault", r"\$\(secret postgres-claims-system-password\)", "hook"),
    ("over TLS", r"PGSSLMODE=require", "hook"),
    (
        "azd generates the agent's password",
        r"secretOrRandomPassword \$\{AZURE_KEY_VAULT_NAME\} postgres-agent-password",
        "params",
    ),
    (
        "azd generates the claims system's",
        r"secretOrRandomPassword \$\{AZURE_KEY_VAULT_NAME\} postgres-claims-system-password",
        "params",
    ),
    ("the vault keeps the agent's", r"name: 'postgres-agent-password'", "vault"),
    ("the vault keeps the claims system's", r"name: 'postgres-claims-system-password'", "vault"),
]


@pytest.mark.discharges("AHC-0040")
@pytest.mark.parametrize(("what", "pattern", "where"), ROLE_HOOK, ids=[r[0] for r in ROLE_HOOK])
def test_the_postprovision_hook_makes_the_logins_from_vault_passwords(
    what: str, pattern: str, where: str
) -> None:
    text = {
        "post": (INFRA / "hooks" / "postprovision.sh").read_text(),
        "hook": DB_ROLES,
        "params": (INFRA / "main.parameters.json").read_text(),
        "vault": (INFRA / "modules" / "keyvault.bicep").read_text(),
    }[where]
    assert re.search(pattern, text), what


def test_the_role_hook_never_prints_a_password() -> None:
    assert os.access(INFRA / "hooks" / "db-roles.sh", os.X_OK)
    for line in DB_ROLES.splitlines():
        if re.match(r"\s*(echo|printf)\b", line):
            assert "PASSWORD" not in line, line


# ------------------------------------------------- A6 the payout limit's store
APPCONFIG = (INFRA / "modules" / "appconfig.bicep").read_text()


def overlay_config(path: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((ROOT / path).read_text())["bindings"]["config"]
    return loaded


# (what, how the infra must say it)
APP_CONFIGURATION: list[tuple[str, Callable[[], bool]]] = [
    (
        "the key both apps read exists under the label they read",
        lambda: (
            "name: 'payout.automatic_limit_inr$${label}'" in APPCONFIG
            and "param label string = 'dev'" in APPCONFIG
        ),
    ),
    (
        "the old key name is gone",
        lambda: "'payout_limit_inr'" not in APPCONFIG,
    ),
    (
        "the label is the one both overlays name",
        lambda: (
            overlay_config("config/azure.yaml")["label"]
            == overlay_config("config/claims-system/azure.yaml")["label"]
            == "dev"
        ),
    ),
    (
        "both identities are given App Configuration Data Reader",
        lambda: (
            re.findall(
                r"identities\.outputs\.(\w+)",
                MODULES["appConfig"][1].split("readerPrincipalIds:")[1].split("]")[0],
            )
            == ["agentPrincipalId", "claimsPrincipalId"]
            and "var dataReader = '516239f1-63e1-4d78-a4de-a74fb236a071'" in APPCONFIG
            and "[for reader in readerPrincipalIds:" in APPCONFIG
            and "principalId: reader" in APPCONFIG
        ),
    ),
    (
        "no app identity may write",
        lambda: (
            "principalId: ownerPrincipalId" in APPCONFIG
            and "ownerPrincipalId: principalId" in MODULES["appConfig"][1]
        ),
    ),
    (
        "A13: the kill switch is agent.enabled under the same label, true unless set",
        lambda: (
            "name: 'agent.enabled$${label}'" in APPCONFIG
            and "param agentEnabled bool = true" in APPCONFIG
            and "value: agentEnabled ? 'true' : 'false'" in APPCONFIG
        ),
    ),
    (
        "both containers are told where the store is",
        lambda: (
            "AZURE_APP_CONFIGURATION_ENDPOINT" in AGENT_ENV
            and "AZURE_APP_CONFIGURATION_ENDPOINT" in CLAIMS_ENV
        ),
    ),
]


@pytest.mark.discharges("P-PAYOUT", "AHC-0057", "AHC-0040")
@pytest.mark.parametrize(
    ("what", "holds"), APP_CONFIGURATION, ids=[a[0] for a in APP_CONFIGURATION]
)
def test_the_payout_limit_is_one_labelled_key_both_apps_may_only_read(
    what: str, holds: Callable[[], bool]
) -> None:
    assert holds(), what


# ------------------------------------------------------------- Tier 4a A9
# Every response says what Content Safety found, in the one header the agent
# records (the guardrail_log evaluator): "<overall>; prompt=<v>; completion=<v>".
# (which response, the policy section it is made in, how the policy says it)
CONTENT_SAFETY_HEADER: list[tuple[str, str, str]] = [
    (
        "a refused model: nothing was screened",
        "inbound",
        r'reason="Model not allowed" />\s*<set-header name="x-content-safety" '
        r'exists-action="override"><value>skipped; prompt=skipped; completion=skipped<',
    ),
    (
        "a blocked prompt: its category, then the parts",
        "inbound",
        r'<value>@\(\(string\)context\.Variables\["promptVerdict"\] \+ "; prompt=" \+ '
        r'\(string\)context\.Variables\["promptVerdict"\] \+ "; completion=skipped"\)',
    ),
    (
        "a blocked completion: its category, then both parts",
        "outbound",
        r'<value>@\(\(string\)context\.Variables\["completionVerdict"\] \+ "; prompt=" \+',
    ),
    (
        "a served response: the overall verdict, then both parts",
        "outbound",
        r'return all \+ "; prompt=" \+ p \+ "; completion=" \+ c;',
    ),
    (
        "an error response: the same",
        "on-error",
        r'return all \+ "; prompt=" \+ p \+ "; completion=" \+ c;',
    ),
]


@pytest.mark.discharges("AHC-0094")
@pytest.mark.parametrize(
    ("which", "section", "pattern"),
    CONTENT_SAFETY_HEADER,
    ids=[c[0] for c in CONTENT_SAFETY_HEADER],
)
def test_every_response_says_what_content_safety_found(
    which: str, section: str, pattern: str
) -> None:
    held = POLICY[POLICY.index(f"<{section}>") : POLICY.index(f"</{section}>")]
    assert re.search(pattern, held), which


@pytest.mark.discharges("AHC-0094")
def test_the_overall_verdict_is_a_block_then_unavailable_then_pass() -> None:
    """The policy's rule, and the evaluator reads the policy's header the same way."""
    from agent_harness.evals.guardrail import verdict_of

    assert 'p.StartsWith("block") ? p : c.StartsWith("block") ? c' in POLICY
    assert '(p == "unavailable" || c == "unavailable") ? "unavailable"' in POLICY
    assert POLICY.count('<set-header name="x-content-safety"') == 5
    for header, overall in [
        ("block:Hate; prompt=block:Hate; completion=skipped", "block:Hate"),
        ("unavailable; prompt=unavailable; completion=pass", "unavailable"),
        ("pass; prompt=pass; completion=skipped", "pass"),
    ]:
        assert verdict_of(header) == overall
