"""Shared fixtures for MCP integration tests.

Loads test config from artifacts/test_config.json (preferred) or TEST_ABS_*
environment variables (fallback). Optionally restores the abs-clean-base
snapshot on the test VM when the kvm-mcp infrastructure is available.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import requests

ARTIFACTS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "agent_planning"
    / "execution"
    / "codebase_refinement"
    / "artifacts"
)


def _load_test_config() -> dict:
    """Load test config from artifacts/test_config.json or env vars.

    Overrides use the TEST_ABS_* namespace (not the bare ABS_* names) because
    importing any abs_mcp module loads abs_mcp/.env into os.environ as a side
    effect, which sets ABS_SERVER_URL/ABS_API_TOKEN to the *production* ABS
    instance. If this fixture read the bare ABS_* names, any test file that
    imports abs_mcp earlier in the same pytest session would silently redirect
    every subsequent test here from the test VM to production. Use TEST_ABS_*
    to explicitly opt into a live override (e.g. from CI) without colliding.
    """
    config_path = ARTIFACTS_DIR / "test_config.json"
    cfg = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    # TEST_ABS_* env vars override any value from test_config.json
    for key, env_key in [
        ("abs_server_url", "TEST_ABS_SERVER_URL"),
        ("abs_api_token", "TEST_ABS_API_TOKEN"),
        ("abs_library_id", "TEST_ABS_LIBRARY_ID"),
    ]:
        env_val = os.environ.get(env_key, "")
        if env_val:
            cfg[key] = env_val
    return cfg


@pytest.fixture(scope="session")
def test_config() -> dict:
    return _load_test_config()


@pytest.fixture(scope="session")
def abs_server_url(test_config: dict) -> str:
    url = test_config.get("abs_server_url", "")
    if not url:
        pytest.skip("abs_server_url not configured (set TEST_ABS_SERVER_URL env or artifacts/test_config.json)")
    return url


@pytest.fixture(scope="session")
def abs_api_token(test_config: dict) -> str:
    token = test_config.get("abs_api_token", "")
    if not token:
        pytest.skip("abs_api_token not configured (set TEST_ABS_API_TOKEN env or artifacts/test_config.json)")
    return token


@pytest.fixture(scope="session")
def abs_library_id(test_config: dict) -> str:
    lib = test_config.get("abs_library_id", "")
    if not lib:
        pytest.skip("abs_library_id not configured")
    return lib


@pytest.fixture(scope="session")
def abs_healthy(abs_server_url: str) -> bool:
    """Confirm ABS /status responds before any test runs."""
    for _ in range(15):
        try:
            resp = requests.get(f"{abs_server_url}/status", timeout=5)
            if resp.ok:
                return True
        except Exception:
            pass
        time.sleep(2)
    pytest.skip(f"ABS at {abs_server_url} not healthy after 30s")


@pytest.fixture(scope="session", autouse=False)
def restore_snapshot(test_config: dict, abs_server_url: str):
    """Restore abs-clean-base snapshot before tests requiring clean state.

    Opt-in via @pytest.mark.usefixtures("restore_snapshot") since restoring
    is destructive (wipes any test data). Skips if virsh unavailable.
    """
    vm_name = test_config.get("vm_name", "abs-test-refinement")
    vm_host = test_config.get("vm_host", "dl380")
    virsh_uri = f"qemu+ssh://root@{vm_host}/system"
    if not shutil.which("virsh"):
        pytest.skip("virsh not available on test runner — snapshot restore skipped")
    subprocess.run(
        ["virsh", "-c", virsh_uri, "destroy", vm_name],
        capture_output=True, timeout=60,
    )
    time.sleep(3)
    result = subprocess.run(
        ["virsh", "-c", virsh_uri, "snapshot-revert", vm_name, "abs-clean-base"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        pytest.skip(f"Snapshot restore failed: {result.stderr.strip()}")
    subprocess.run(
        ["virsh", "-c", virsh_uri, "start", vm_name],
        capture_output=True, timeout=60,
    )
    for _ in range(30):
        try:
            resp = requests.get(f"{abs_server_url}/status", timeout=5)
            if resp.ok:
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        pytest.skip("ABS did not become healthy within 60s after snapshot restore")
    yield
