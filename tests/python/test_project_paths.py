"""Regression tests for the project-local path contract.

A worker may be started without the Tauri launcher (direct Python, pytest, a
third-party library). In that case no PYMSS_* variables are injected, so the
code defaults alone must keep every write inside the checkout.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if __package__:
    from . import _bootstrap as _worker_test_bootstrap  # noqa: F401
else:
    import _bootstrap as _worker_test_bootstrap  # noqa: F401

import _project_paths as paths


class ProjectPathsTest(unittest.TestCase):
    def setUp(self):
        self._saved = {key: value for key, value in os.environ.items() if key.startswith("PYMSS_")}
        for key in list(os.environ):
            if key.startswith("PYMSS_"):
                del os.environ[key]

    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("PYMSS_"):
                del os.environ[key]
        os.environ.update(self._saved)

    def test_project_root_is_the_checkout(self):
        self.assertEqual(paths.PROJECT_ROOT, REPO_ROOT)
        self.assertEqual(paths.ENV_ROOT, REPO_ROOT / ".venv")

    def test_default_layout(self):
        expected = {
            "data_root": REPO_ROOT / "data",
            "models_dir": REPO_ROOT / "data" / "models",
            "settings_dir": REPO_ROOT / "data" / "settings",
            "debug_dir": REPO_ROOT / "data" / "debug",
            "plugins_dir": REPO_ROOT / "data" / "plugins",
            "user_models_file": REPO_ROOT / "data" / "settings" / "user_models.json",
            "runtime_envs_dir": REPO_ROOT / ".venv" / "python" / "runtime-envs",
            "scratch_dir": REPO_ROOT / "data" / "temp",
        }
        actual = paths.default_paths()
        self.assertEqual(set(actual), set(expected))
        for name, want in expected.items():
            self.assertEqual(actual[name].resolve(), want.resolve(), name)

    def test_every_default_is_project_local(self):
        home = Path.home().resolve()
        forbidden = [
            home / ".cache" / "pymss",
            home / ".cache" / "pymss-studio",
            home / ".pymss",
            home / ".pymss-studio",
        ]
        for name, value in paths.default_paths().items():
            resolved = value.resolve()
            self.assertTrue(paths.is_project_local(resolved), "%s -> %s" % (name, resolved))
            for bad in forbidden:
                self.assertFalse(resolved == bad or bad in resolved.parents, "%s -> %s" % (name, resolved))

    def test_explicit_overrides_still_win(self):
        fake = REPO_ROOT / "_debug_output" / "tmp" / "_override_probe"
        env = {
            "PYMSS_STUDIO_DATA_ROOT": str(fake / "custom-data"),
            "PYMSS_MODEL_DIR": str(fake / "models"),
            "PYMSS_PLUGINS_DIR": str(fake / "plugins"),
            "PYMSS_USER_MODELS": str(fake / "user_models.json"),
            "PYMSS_STUDIO_DEBUG_DIR": str(fake / "debug"),
            "PYMSS_STUDIO_RUNTIME_ENVS_DIR": str(fake / "runtimes"),
        }
        with mock.patch.dict(os.environ, env):
            self.assertEqual(paths.data_root(), Path(env["PYMSS_STUDIO_DATA_ROOT"]))
            self.assertEqual(paths.models_dir(), Path(env["PYMSS_MODEL_DIR"]))
            self.assertEqual(paths.plugins_dir(), Path(env["PYMSS_PLUGINS_DIR"]))
            self.assertEqual(paths.user_models_file(), Path(env["PYMSS_USER_MODELS"]))
            self.assertEqual(paths.debug_dir(), Path(env["PYMSS_STUDIO_DEBUG_DIR"]))
            self.assertEqual(paths.runtime_envs_dir(), Path(env["PYMSS_STUDIO_RUNTIME_ENVS_DIR"]))

    def test_runtime_envs_override_variable(self):
        fake = REPO_ROOT / "_debug_output" / "tmp" / "_override_probe" / "via-override"
        with mock.patch.dict(os.environ, {"PYMSS_STUDIO_RUNTIME_ENVS_OVERRIDE": str(fake)}):
            self.assertEqual(paths.runtime_envs_dir(), fake)

    def test_project_local_or_redirects_strays(self):
        inside = REPO_ROOT / "data" / "somewhere"
        stray = Path.home().resolve() / "definitely-outside-soundflow"
        self.assertEqual(paths.project_local_or(inside), inside)
        self.assertEqual(paths.project_local_or(stray), paths.runtime_envs_dir())
        self.assertEqual(paths.project_local_or(stray, inside), inside)

    def test_is_project_local_rejects_outside_paths(self):
        self.assertTrue(paths.is_project_local(REPO_ROOT / "data" / "models"))
        self.assertFalse(paths.is_project_local(Path.home() / "somewhere-else"))
        self.assertFalse(paths.is_project_local(Path.home().anchor + "Windows"))

    def test_worker_fallbacks_are_project_local(self):
        import worker_bootstrap
        import worker_custom_models
        import worker_models
        import worker_optional_packages
        import worker_plugins

        checks = {
            "worker_models._debug_dir": worker_models._debug_dir(),
            "worker_models._default_model_dir": worker_models._default_model_dir(),
            "worker_custom_models._sidecar_path": worker_custom_models._sidecar_path(),
            "worker_custom_models.managed_root": worker_custom_models.managed_root(),
            "worker_optional_packages._runtime_envs_dir": worker_optional_packages._runtime_envs_dir(),
            "worker_bootstrap._default_runtime_envs_dir": worker_bootstrap._default_runtime_envs_dir(),
            "worker_plugins._directory_plugins (read-only)": paths.plugins_dir(),
        }
        for name, value in checks.items():
            self.assertTrue(paths.is_project_local(value), "%s -> %s" % (name, value))
        self.assertIsInstance(worker_plugins._directory_plugins(), list)

    def test_no_home_fallback_left_in_worker_sources(self):
        sources = [
            "python/_project_paths.py",
            "python/worker_bootstrap.py",
            "python/worker_custom_models.py",
            "python/worker_models.py",
            "python/worker_operation_metadata.py",
            "python/worker_optional_packages.py",
            "python/worker_plugins.py",
            "python/runtime_audio_probe.py",
        ]
        for rel in sources:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("Path.home()", text, rel)

    def test_probe_scratch_directories_are_pinned(self):
        text = (REPO_ROOT / "python" / "runtime_audio_probe.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("dir=str(scratch_dir())"), 2)
        self.assertTrue(paths.is_project_local(paths.scratch_dir()))


if __name__ == "__main__":
    unittest.main()
