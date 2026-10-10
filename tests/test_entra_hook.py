"""A2 — the Entra hook's test users: asked for first, made once, passwords only in Key Vault.

`infra/hooks/entra-app.sh` run for real, offline: `az` and `azd` on its PATH
are fakes that answer from this test's tenant (which test users exist already)
and record every call — its arguments, and what came on standard input. No
call reaches Entra or Azure. The rows hold the hook to what it promises:
nothing is created without a yes, an existing user is never remade, a new
user's password goes to Key Vault before Graph sees it and appears in no
argument and no output, and a re-run only brings the attribute and the role
up to date.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "infra" / "hooks" / "entra-app.sh"
DOMAIN = "fnol-test.onmicrosoft.com"

FAKE_AZ = r"""#!/usr/bin/env bash
# The tenant as this test describes it; every call recorded.
printf '%s\n' "$*" >> "$FAKE_DIR/argv"
case "$*" in
  *"keyvault secret set"*|*"--body @/dev/stdin"*)
    { printf '%s\t' "$*"; cat; printf '\n'; } >> "$FAKE_DIR/stdin" ;;
esac
case "$*" in
  "ad app list"*) echo "app-$(echo "$*" | sed -E 's/.*--display-name ([^ ]+).*/\1/')" ;;
  "ad app show"*) echo "obj-1" ;;
  "ad sp list"*) echo "sp-1" ;;
  "ad signed-in-user show"*) echo "owner-1" ;;
  "ad user show --id "*)
    upn="$(echo "$*" | sed -E 's/.*--id ([^ ]+).*/\1/')"
    if grep -qx "$upn" "$FAKE_DIR/existing" || grep -q "userPrincipalName\": \"$upn" "$FAKE_DIR/stdin" 2>/dev/null; then
      echo "id-${upn%%@*}"
    else
      echo "ERROR: user not found" >&2; exit 3
    fi ;;
  "rest --method GET"*"/domains"*) echo "__DOMAIN__" ;;
  "rest --method GET"*"extensionProperties"*) echo 1 ;;
  "rest --method GET"*"appRoleAssignedTo"*) echo 0 ;;
  "rest --method GET"*"oauth2PermissionGrants"*) echo "grant-1" ;;
  "keyvault secret show"*) echo false ;;
esac
exit 0
""".replace("__DOMAIN__", DOMAIN)

USERS = ("rohan", "meera", "asha")
ROWS = [
    # (row, users already in the tenant, the hook's arguments, users it must create)
    ("none exist and no yes: nothing is created", (), [], ()),
    ("none exist and --yes: all three are created", (), ["--yes"], USERS),
    ("one is missing: only that one is created", ("rohan", "asha"), ["--yes"], ("meera",)),
    ("all exist: none is remade, the rest kept current", USERS, ["--yes"], ()),
]


def run_hook(
    tmp: Path, existing: tuple[str, ...], args: list[str], **env: str
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "az").write_text(FAKE_AZ)
    (bin_dir / "azd").write_text(
        '#!/usr/bin/env bash\nprintf "azd %s\\n" "$*" >> "$FAKE_DIR/argv"\n'
    )
    for tool in ("az", "azd"):
        (bin_dir / tool).chmod(0o755)
    (tmp / "existing").write_text("".join(f"{u}@{DOMAIN}\n" for u in existing))
    environment = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DIR": str(tmp),
        "AZURE_KEY_VAULT_NAME": "kv-test",
        "ENTRA_REDIRECT_URI": "https://ca-fnol-agent.example.test/signin/callback",
        "HOLDER_CLAIM": "extn.customer_id",
        **env,
    }
    return subprocess.run(
        ["bash", str(HOOK), *args],
        env=environment,
        stdin=subprocess.DEVNULL,  # no terminal: only --yes may create
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.discharges("AHC-0040")
@pytest.mark.parametrize(("row", "existing", "args", "created"), ROWS, ids=[r[0] for r in ROWS])
def test_the_hook_makes_the_test_users(
    tmp_path: Path, row: str, existing: tuple[str, ...], args: list[str], created: tuple[str, ...]
) -> None:
    done = run_hook(tmp_path, existing, args)
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "argv").read_text().splitlines()
    piped = (tmp_path / "stdin").read_text().splitlines() if created else []
    made = [json.loads(line.split("\t", 1)[1]) for line in piped if "/users" in line]
    assert sorted(u["mailNickname"] for u in made) == sorted(created), row
    missing = [u for u in USERS if u not in existing]
    if missing and not args:
        assert "Test users to create" in done.stdout and "--yes" in done.stdout
    for user in made:
        password = user["passwordProfile"]["password"]
        vault = [i for i, line in enumerate(piped) if f"test-user-{user['mailNickname']}-" in line]
        graph = [i for i, line in enumerate(piped) if password in line and "/users" in line]
        assert vault and graph and vault[0] < graph[0], "to Key Vault before Graph sees it"
        assert piped[vault[0]].endswith(password), "the vault holds the same password"
        assert user["passwordProfile"]["forceChangePasswordNextSignIn"] is True
        assert not any(password in c for c in calls), "never an argument"
        assert password not in done.stdout + done.stderr, "never in the output"
    linked = [c for c in calls if "PATCH" in c and "/users/" in c]
    if args:  # every test user present afterwards gets the attribute; Asha the role
        holders = {"rohan": "PH-1001", "meera": "PH-1002"}
        for login, holder in holders.items():
            assert any(f"id-{login}" in c and holder in c for c in linked), login
        assert any("appRoleAssignedTo" in c and "id-asha" in c for c in calls if "POST" in c)
    else:
        assert not linked, "no yes: no user is touched"


@pytest.mark.discharges("AHC-0040")
def test_a_holder_claim_entra_cannot_emit_is_refused(tmp_path: Path) -> None:
    done = run_hook(tmp_path, USERS, ["--yes"], HOLDER_CLAIM="customer_id")
    assert done.returncode == 1 and "extn.<attribute>" in done.stderr
    assert not (tmp_path / "argv").exists(), "refused before any call"
