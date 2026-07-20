#!/usr/bin/env python3
"""Peer CLI subprocesses must not inherit lifecycle role evidence identity."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import sdtd_adversarial_cli_bridge as adversarial  # noqa: E402
import sdtd_multiview_cli_bridge as multiview  # noqa: E402


IDENTITY = {
    "BUGATE_AGENT_ROLE": "designer",
    "BUGATE_SESSION_ID": "designer-session",
    "BUGATE_ROLE_STATE": "pre_code",
    "BUGATE_RECEIPT_SHA256": "receipt-secret-like-value",
    "BUGATE_HANDOFF_ID": "handoff-secret-like-value",
    "BUGATE_SESSION_TOKEN": "future-session-identity",
}
PRESERVED = {
    "BUGATE_PROFILE": "bugate.profile.yaml",
    "BUGATE_PROJECT_ROOT": "/tmp/example-workspace",
    "SDTD_CODEX_MODEL": "test-codex-model",
    "SDTD_CLAUDE_MODEL": "test-claude-model",
    "SDTD_CODEX_REASONING_EFFORT": "high",
    "SDTD_CLAUDE_EFFORT": "high",
    "SDTD_CLI_TIMEOUT_SECONDS": "321",
    "SDTD_CODEX_SKIP_GIT_REPO_CHECK": "1",
}


@contextmanager
def bridge_environment(*, proxy_enabled: bool):
    previous = os.environ.copy()
    try:
        os.environ.update(IDENTITY)
        os.environ.update(PRESERVED)
        os.environ.update(
            {
                "SDTD_CLI_PROXY": "1" if proxy_enabled else "0",
                "SDTD_CLI_HTTPS_PROXY": "http://proxy.invalid:8443",
                "SDTD_CLI_HTTP_PROXY": "http://proxy.invalid:8080",
                "SDTD_CLI_ALL_PROXY": "socks5://proxy.invalid:1080",
                "HTTPS_PROXY": "http://parent-proxy.invalid:9443",
                "HTTP_PROXY": "http://parent-proxy.invalid:9080",
                "ALL_PROXY": "socks5://parent-proxy.invalid:1081",
            }
        )
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


class PeerRoleEnvironmentTests(unittest.TestCase):
    def _reload(self, module):
        return importlib.reload(module)

    def _assert_sanitized(self, env: dict[str, str]) -> None:
        for key in IDENTITY:
            self.assertNotIn(key, env, key)
        for key, value in PRESERVED.items():
            self.assertEqual(value, env.get(key), key)

    def test_cli_env_strips_identity_with_proxy_enabled_and_disabled(self) -> None:
        for proxy_enabled in (True, False):
            for original in (multiview, adversarial):
                with self.subTest(
                    bridge=original.__name__, proxy_enabled=proxy_enabled
                ), bridge_environment(proxy_enabled=proxy_enabled):
                    module = self._reload(original)
                    child = module.cli_env()
                    self._assert_sanitized(child)
                    self.assertEqual(
                        "1" if proxy_enabled else "0", child.get("SDTD_CLI_PROXY")
                    )
                    self.assertEqual(
                        "http://proxy.invalid:8080",
                        child.get("SDTD_CLI_HTTP_PROXY"),
                    )
                    if proxy_enabled:
                        self.assertEqual(
                            "http://proxy.invalid:8080", child.get("http_proxy")
                        )
                        self.assertEqual(
                            "http://proxy.invalid:8080", child.get("HTTP_PROXY")
                        )
                    else:
                        self.assertEqual(
                            "http://parent-proxy.invalid:9080", child.get("HTTP_PROXY")
                        )

    def test_run_peer_cli_passes_only_sanitized_environment(self) -> None:
        for proxy_enabled in (True, False):
            for original in (multiview, adversarial):
                with self.subTest(
                    bridge=original.__name__, proxy_enabled=proxy_enabled
                ), bridge_environment(proxy_enabled=proxy_enabled):
                    module = self._reload(original)
                    captured: dict[str, str] = {}

                    def fake_run(command, **kwargs):
                        captured.update(kwargs["env"])
                        return subprocess.CompletedProcess(
                            command, 0, stdout="peer output", stderr=""
                        )

                    with mock.patch.object(module, "build_command", return_value=["peer"]), mock.patch.object(
                        module.subprocess, "run", side_effect=fake_run
                    ):
                        rc, stdout, stderr = module.run_peer_cli("claude", "prompt")
                    self.assertEqual((0, "peer output", ""), (rc, stdout, stderr))
                    self._assert_sanitized(captured)

    def test_codex_skip_git_repo_check_is_explicit_opt_in(self) -> None:
        for enabled in (False, True):
            for original in (multiview, adversarial):
                with self.subTest(
                    bridge=original.__name__, enabled=enabled
                ), bridge_environment(proxy_enabled=False):
                    os.environ["SDTD_CODEX_SKIP_GIT_REPO_CHECK"] = (
                        "1" if enabled else "0"
                    )
                    module = self._reload(original)
                    with mock.patch.object(
                        module, "codex_supports_ask_for_approval", return_value=False
                    ):
                        command = module.build_command("codex")
                    self.assertEqual(
                        enabled,
                        "--skip-git-repo-check" in command,
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
