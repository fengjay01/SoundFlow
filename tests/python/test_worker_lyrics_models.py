from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

if __package__:
    from . import _bootstrap as _worker_test_bootstrap  # noqa: F401
else:
    import _bootstrap as _worker_test_bootstrap  # noqa: F401

import worker_lyrics_models
import worker_models
from lyrics.asr import resolve_align_model_source, resolve_asr_model_source
from worker_lyrics_models import (
    LYRICS_MODELS,
    cache_name_for_repo,
    cmd_lyrics_model_download,
    cmd_lyrics_model_status,
    find_lyrics_model,
    is_complete_lyrics_model_cache,
    lyrics_model_role,
    lyrics_models_root,
    missing_files_for,
    repo_for_cache_name,
    status_payload,
)


def write_complete_tiny(dir_path: Path) -> None:
    """Minimal complete faster-whisper-tiny cache (flat layout)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "config.json").write_text("{}", encoding="utf-8")
    (dir_path / "model.bin").write_bytes(b"w" * 2048)
    (dir_path / "tokenizer.json").write_text("{}", encoding="utf-8")


class CatalogTests(unittest.TestCase):
    def test_catalog_contains_expected_keys(self):
        self.assertEqual(
            set(LYRICS_MODELS),
            {
                "faster-whisper-tiny",
                "faster-whisper-base",
                "faster-whisper-small",
                "faster-whisper-medium",
                "align-zh",
                "align-en",
            },
        )

    def test_find_lyrics_model_accepts_key_repo_and_cache_name(self):
        entry = LYRICS_MODELS["faster-whisper-small"]
        self.assertIs(find_lyrics_model("faster-whisper-small"), entry)
        self.assertIs(find_lyrics_model("Systran/faster-whisper-small"), entry)
        self.assertIs(find_lyrics_model("Systran--faster-whisper-small"), entry)
        self.assertIsNone(find_lyrics_model("does-not-exist"))
        self.assertIsNone(find_lyrics_model(""))

    def test_cache_name_roundtrip(self):
        self.assertEqual(cache_name_for_repo("a/b"), "a--b")
        self.assertEqual(repo_for_cache_name("a--b"), "a/b")

    def test_lyrics_model_role(self):
        self.assertEqual(lyrics_model_role("jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"), "alignment")
        self.assertEqual(lyrics_model_role("align-zh"), "alignment")
        self.assertEqual(lyrics_model_role("Systran/faster-whisper-small"), "recognition")


class CompletenessTests(unittest.TestCase):
    def test_missing_dir_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_value:
            self.assertFalse(is_complete_lyrics_model_cache(Path(temp_value) / "nope"))

    def test_complete_cache_is_complete(self):
        with tempfile.TemporaryDirectory() as temp_value:
            target = Path(temp_value) / "Systran--faster-whisper-small"
            write_complete_tiny(target)
            self.assertTrue(is_complete_lyrics_model_cache(target))

    def test_missing_required_file_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_value:
            target = Path(temp_value) / "Systran--faster-whisper-small"
            write_complete_tiny(target)
            (target / "tokenizer.json").unlink()
            self.assertFalse(is_complete_lyrics_model_cache(target))
            self.assertIn("tokenizer.json", missing_files_for(target, LYRICS_MODELS["faster-whisper-small"]))

    def test_lfs_pointer_weight_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_value:
            target = Path(temp_value) / "Systran--faster-whisper-small"
            write_complete_tiny(target)
            (target / "model.bin").write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
            self.assertFalse(is_complete_lyrics_model_cache(target))

    def test_undersized_weight_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_value:
            target = Path(temp_value) / "Systran--faster-whisper-small"
            write_complete_tiny(target)
            (target / "model.bin").write_bytes(b"tiny")
            self.assertFalse(is_complete_lyrics_model_cache(target))

    def test_part_remnant_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_value:
            target = Path(temp_value) / "Systran--faster-whisper-small"
            write_complete_tiny(target)
            (target / "model.bin.part").write_bytes(b"x")
            self.assertFalse(is_complete_lyrics_model_cache(target))

    def test_unknown_cache_name_falls_back_to_config_plus_weight(self):
        with tempfile.TemporaryDirectory() as temp_value:
            target = Path(temp_value) / "custom--thing"
            target.mkdir()
            (target / "config.json").write_text("{}", encoding="utf-8")
            (target / "pytorch_model.bin").write_bytes(b"w" * 2048)
            self.assertTrue(is_complete_lyrics_model_cache(target))

    def test_align_zh_requires_vocab_not_tokenizer(self):
        # HF 权威清单里没有 tokenizer.json（该模型走 vocab.json 分词）。
        with tempfile.TemporaryDirectory() as temp_value:
            target = Path(temp_value) / "jonatasgrosman--wav2vec2-large-xlsr-53-chinese-zh-cn"
            target.mkdir()
            for name in ("config.json", "preprocessor_config.json", "special_tokens_map.json", "vocab.json"):
                (target / name).write_text("{}", encoding="utf-8")
            (target / "pytorch_model.bin").write_bytes(b"w" * 2048)
            self.assertTrue(is_complete_lyrics_model_cache(target))
            (target / "vocab.json").unlink()
            self.assertFalse(is_complete_lyrics_model_cache(target))
            self.assertIn("vocab.json", missing_files_for(target, LYRICS_MODELS["align-zh"]))


class RootAndStatusTests(unittest.TestCase):
    def test_lyrics_models_root_layout(self):
        root = lyrics_models_root("/base/models")
        self.assertEqual(root, Path("/base/models") / "_tool_models" / "lyrics" / "models")

    def test_status_payload_reports_models(self):
        with tempfile.TemporaryDirectory() as temp_value:
            write_complete_tiny(
                Path(temp_value) / "_tool_models" / "lyrics" / "models" / "Systran--faster-whisper-tiny"
            )
            payload = status_payload(temp_value)

        self.assertEqual(payload["tool"], "lyrics")
        self.assertEqual(payload["modelDir"], str(lyrics_models_root(temp_value)))
        self.assertEqual(payload["completeCount"], 1)
        self.assertEqual(len(payload["models"]), len(LYRICS_MODELS))
        by_key = {item["key"]: item for item in payload["models"]}
        self.assertTrue(by_key["faster-whisper-tiny"]["complete"])
        self.assertFalse(by_key["faster-whisper-small"]["complete"])
        self.assertIn("config.json", by_key["faster-whisper-small"]["missingFiles"])
        self.assertEqual(by_key["faster-whisper-tiny"]["tool"], "lyrics")
        self.assertEqual(by_key["align-zh"]["role"], "alignment")


class CmdStatusTests(unittest.TestCase):
    def test_cmd_status_emits_payload(self):
        with mock.patch("worker_lyrics_models.emit") as events:
            result = cmd_lyrics_model_status({"modelDir": None})
        self.assertEqual(result, 0)
        self.assertEqual(events.call_args.args[0], "lyrics_model_status")
        payload = events.call_args.args[1]
        self.assertIn("models", payload)
        self.assertEqual(len(payload["models"]), len(LYRICS_MODELS))


class CmdDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = self.enterContext(mock.patch("worker_lyrics_models.emit"))
        self.failures = self.enterContext(mock.patch("worker_lyrics_models.emit_error", return_value=1))
        self.download_calls: list[str] = []
        self.seen_endpoints: list[str] = []
        self.listing = [
            {"path": "config.json", "size": 4},
            {"path": "model.bin", "size": 2048},
            {"path": "tokenizer.json", "size": 2},
        ]

        def fake_download(repo, rel_path, dest, expected_size, timeout, endpoints, open_request, on_bytes, task_id=None):
            self.download_calls.append(rel_path)
            self.seen_endpoints = list(endpoints)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x" * (expected_size or 8))
            if on_bytes is not None:
                on_bytes(expected_size or 8)
            return endpoints[0]

        self.enterContext(mock.patch.object(worker_lyrics_models, "_download_one", side_effect=fake_download))

    def tearDown(self):
        self.temp.cleanup()

    def command(self, **payload):
        with mock.patch.object(
            worker_lyrics_models, "list_repo_files",
            return_value=[dict(item) for item in self.listing],
        ):
            return cmd_lyrics_model_download({
                "model": "faster-whisper-tiny",
                "modelDir": str(self.root),
                "taskId": "lyrics_model_test",
                **payload,
            })

    def event_types(self):
        return [call.args[0] for call in self.events.call_args_list]

    def test_unknown_model_reports_not_found(self):
        result = cmd_lyrics_model_download({"model": "nope", "modelDir": str(self.root)})
        self.assertEqual(result, 1)
        self.assertEqual(self.failures.call_args.args[0], "LYRICS_MODEL_NOT_FOUND")

    def test_happy_path_downloads_and_completes(self):
        self.assertEqual(self.command(), 0)

        types = self.event_types()
        self.assertEqual(types[0], "download_started")
        self.assertIn("download_stage", types)
        self.assertEqual(types[-1], "download_done")
        self.assertEqual(self.download_calls, ["config.json", "model.bin", "tokenizer.json"])

        target = (self.root / "_tool_models" / "lyrics" / "models" / "asr"
                  / "Systran--faster-whisper-tiny")
        self.assertTrue(is_complete_lyrics_model_cache(target))
        done_payload = self.events.call_args_list[-1].args[1]
        self.assertEqual(done_payload["downloaded"], ["config.json", "model.bin", "tokenizer.json"])
        self.assertEqual(done_payload["skipped"], [])
        self.assertEqual(done_payload["progress"], 100)
        self.assertEqual(self.events.call_args_list[-1].kwargs.get("task_id"), "lyrics_model_test")

    def test_existing_complete_file_is_skipped(self):
        target = (self.root / "_tool_models" / "lyrics" / "models" / "asr"
                  / "Systran--faster-whisper-tiny")
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_bytes(b"x" * 4)

        self.assertEqual(self.command(), 0)

        self.assertEqual(self.download_calls, ["model.bin", "tokenizer.json"])
        done_payload = self.events.call_args_list[-1].args[1]
        self.assertEqual(done_payload["skipped"], ["config.json"])

    def test_empty_listing_fails_download(self):
        self.listing = []
        self.assertEqual(self.command(), 1)
        self.assertEqual(self.failures.call_args.args[0], "LYRICS_MODEL_DOWNLOAD_FAILED")
        self.assertNotIn("download_done", self.event_types())

    def test_framework_and_duplicate_weights_are_skipped(self):
        self.listing = [
            {"path": "config.json", "size": 4},
            {"path": "preprocessor_config.json", "size": 2},
            {"path": "vocab.json", "size": 2},
            {"path": "special_tokens_map.json", "size": 2},
            {"path": "model.safetensors", "size": 2048},
            {"path": "pytorch_model.bin", "size": 2048},
            {"path": "tf_model.h5", "size": 2048},
            {"path": "flax_model.msgpack", "size": 2048},
        ]
        self.assertEqual(self.command(model="align-en"), 0)
        self.assertEqual(
            self.download_calls,
            ["config.json", "preprocessor_config.json", "vocab.json", "special_tokens_map.json", "model.safetensors"],
        )
        target = (self.root / "_tool_models" / "lyrics" / "models" / "asr"
                  / "facebook--wav2vec2-base-960h")
        self.assertTrue(is_complete_lyrics_model_cache(target))

    def test_incomplete_result_fails_download(self):
        # Drop tokenizer.json so the finished cache cannot validate.
        self.listing = self.listing[:2]
        self.assertEqual(self.command(), 1)
        self.assertEqual(self.failures.call_args.args[0], "LYRICS_MODEL_DOWNLOAD_FAILED")
        self.assertNotIn("download_done", self.event_types())

    def test_download_error_maps_to_download_failed(self):
        with mock.patch.object(
            worker_lyrics_models, "_download_one",
            side_effect=RuntimeError("boom"),
        ):
            self.assertEqual(self.command(), 1)
        self.assertEqual(self.failures.call_args.args[0], "LYRICS_MODEL_DOWNLOAD_FAILED")
        self.assertNotIn("download_done", self.event_types())

    def test_mirror_candidates_reach_download(self):
        with mock.patch.dict(worker_lyrics_models.os.environ, {"HF_ENDPOINT": ""}, clear=False):
            self.command()
        self.assertEqual(self.seen_endpoints[0], "https://hf-mirror.com")
        self.assertIn("https://huggingface.co", self.seen_endpoints)

    def test_explicit_endpoint_wins(self):
        with mock.patch.dict(worker_lyrics_models.os.environ, {"HF_ENDPOINT": ""}, clear=False):
            self.command(endpoint="https://mirror.example.com")
        self.assertEqual(self.seen_endpoints[0], "https://mirror.example.com")


class CandidateEndpointsTests(unittest.TestCase):
    def test_order_and_dedup(self):
        with mock.patch.dict(worker_lyrics_models.os.environ, {"HF_ENDPOINT": "https://a.example.com/"}, clear=False):
            endpoints = worker_lyrics_models.candidate_endpoints("https://b.example.com")
        self.assertEqual(endpoints, [
            "https://b.example.com",
            "https://a.example.com",
            "https://hf-mirror.com",
            "https://huggingface.co",
        ])

    def test_defaults(self):
        with mock.patch.dict(worker_lyrics_models.os.environ, {"HF_ENDPOINT": ""}, clear=False):
            endpoints = worker_lyrics_models.candidate_endpoints(None)
        self.assertEqual(endpoints, ["https://hf-mirror.com", "https://huggingface.co"])


class _FakeResponse:
    def __init__(self, status: int, chunks: list[bytes]):
        self.status = status
        self._chunks = list(chunks)

    def read(self, size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class DownloadOneResumeTests(unittest.TestCase):
    """Integration-ish coverage for the resume / failover transfer engine."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.enterContext(mock.patch.object(worker_lyrics_models.time, "sleep", lambda _s: None))

    def tearDown(self):
        self.temp.cleanup()

    def test_truncated_stream_resumes_with_range(self):
        payload = b"A" * 1000
        dest = self.root / "m" / "model.bin"
        part = dest.with_name("model.bin.part")
        requests: list[dict] = []
        state = {"attempt": 0}

        class FirstResponder:
            sent = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size: int = -1) -> bytes:
                if FirstResponder.sent:
                    return b""
                # Write 400 bytes then break the stream mid-transfer.
                FirstResponder.sent = True
                dest.parent.mkdir(parents=True, exist_ok=True)
                with part.open("ab") as fh:
                    fh.write(payload[:400])
                raise ConnectionResetError("stream reset")

        def open_request(request, timeout):
            state["attempt"] += 1
            requests.append({"range": request.headers.get("Range")})
            if state["attempt"] == 1:
                return FirstResponder()
            return _FakeResponse(206, [payload[400:]])

        on_bytes = lambda _n: None
        served = worker_lyrics_models._download_one(
            "o/m", "model.bin", dest, len(payload), 5,
            ["https://hf-mirror.com", "https://huggingface.co"],
            open_request, on_bytes, "task",
        )
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(requests[1]["range"], "bytes=400-")
        self.assertEqual(dest.read_bytes(), payload)
        self.assertFalse(dest.with_name("model.bin.part").exists())

    def test_endpoint_failover_on_connect_error(self):
        payload = b"B" * 100
        dest = self.root / "m" / "model.bin"
        attempts: list[str] = []

        def open_request(request, timeout):
            host = urllib.parse.urlsplit(request.full_url).netloc
            attempts.append(host)
            if host == "hf-mirror.com":
                raise urllib.error.URLError("connection refused")
            return _FakeResponse(200, [payload])

        on_bytes = lambda _n: None
        served = worker_lyrics_models._download_one(
            "o/m", "model.bin", dest, len(payload), 5,
            ["https://hf-mirror.com", "https://huggingface.co"],
            open_request, on_bytes, "task",
        )
        self.assertEqual(served, "https://huggingface.co")
        self.assertEqual(attempts, ["hf-mirror.com", "huggingface.co"])
        self.assertEqual(dest.read_bytes(), payload)

    def test_http_404_is_not_retried(self):
        dest = self.root / "m" / "model.bin"
        attempts = {"n": 0}

        def open_request(request, timeout):
            attempts["n"] += 1
            raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            worker_lyrics_models._download_one(
                "o/m", "model.bin", dest, 100, 5,
                ["https://hf-mirror.com", "https://huggingface.co"],
                open_request, lambda _n: None, "task",
            )
        self.assertEqual(attempts["n"], 1)

    def test_permanent_give_up_after_max_attempts(self):
        dest = self.root / "m" / "model.bin"

        def open_request(request, timeout):
            raise ConnectionResetError("always broken")

        with self.assertRaises(RuntimeError) as caught:
            worker_lyrics_models._download_one(
                "o/m", "model.bin", dest, 100, 5,
                ["https://hf-mirror.com"],
                open_request, lambda _n: None, "task",
            )
        self.assertIn("after 6 attempts", str(caught.exception))


class ListFailoverTests(unittest.TestCase):
    def test_failover_uses_next_endpoint(self):
        calls: list[str] = []

        def fake_list(repo, endpoint, timeout):
            calls.append(endpoint)
            if endpoint == "https://hf-mirror.com":
                raise urllib.error.URLError("down")
            return [{"path": "config.json", "size": 2}]

        with mock.patch.object(worker_lyrics_models, "list_repo_files", side_effect=fake_list):
            files, endpoint = worker_lyrics_models.list_repo_files_with_failover(
                "o/m", ["https://hf-mirror.com", "https://huggingface.co"], 5)
        self.assertEqual(endpoint, "https://huggingface.co")
        self.assertEqual(files, [{"path": "config.json", "size": 2}])
        self.assertEqual(calls, ["https://hf-mirror.com", "https://huggingface.co"])

    def test_all_fail(self):
        with mock.patch.object(
            worker_lyrics_models, "list_repo_files",
            side_effect=urllib.error.URLError("down"),
        ):
            with self.assertRaises(RuntimeError):
                worker_lyrics_models.list_repo_files_with_failover(
                    "o/m", ["https://a", "https://b"], 5)


class ResolverTests(unittest.TestCase):
    def test_resolve_asr_model_source_maps_size_to_cache_name(self):
        with tempfile.TemporaryDirectory() as temp_value:
            root = Path(temp_value)
            write_complete_tiny(root / "Systran--faster-whisper-tiny")
            expected = str(root / "Systran--faster-whisper-tiny")
            self.assertEqual(resolve_asr_model_source("tiny", str(root)), expected)
            self.assertEqual(resolve_asr_model_source("faster-whisper-tiny", str(root)), expected)
            self.assertEqual(resolve_asr_model_source("Systran/faster-whisper-tiny", str(root)), expected)
            # 管理目录没有 small → 原名回落（whisperx 自行探测/报错）
            self.assertEqual(resolve_asr_model_source("small", str(root)), "small")
            # 显式本地目录直用
            self.assertEqual(resolve_asr_model_source(expected, str(root)), expected)

    def test_resolve_align_model_source_priority(self):
        with tempfile.TemporaryDirectory() as temp_value:
            root = Path(temp_value)
            target = root / "jonatasgrosman--wav2vec2-large-xlsr-53-chinese-zh-cn"
            target.mkdir()
            (target / "config.json").write_text("{}", encoding="utf-8")
            # 管理目录命中
            self.assertEqual(resolve_align_model_source(None, "zh", str(root)), str(target))
            # 显式指定直用
            self.assertEqual(resolve_align_model_source("my/align", "zh", str(root)), "my/align")
            # 未命中 → HF id 探测系统缓存
            self.assertEqual(
                resolve_align_model_source(None, "zh", str(root / "empty")),
                "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
            )
            # 其他语言 → None（whisperx 默认表）
            self.assertIsNone(resolve_align_model_source(None, "ja", str(root)))


class WorkerModelsIntegrationTests(unittest.TestCase):
    def _stub_registry(self, root: Path):
        registry = mock.Mock()
        registry.model_root.return_value = root
        registry.list_models.return_value = []
        return registry

    def test_lyrics_tool_model_appears_in_storage_summary(self):
        with tempfile.TemporaryDirectory() as temp_value:
            root = Path(temp_value)
            write_complete_tiny(
                root / "_tool_models" / "lyrics" / "models" / "Systran--faster-whisper-tiny"
            )
            registry = self._stub_registry(root)
            with mock.patch.dict("sys.modules", {
                "pymss": mock.Mock(),
                "pymss.model_registry": registry,
            }):
                summary = worker_models._storage_summary_payload(str(root))

        self.assertEqual(len(summary["toolModels"]), 1)
        item = summary["toolModels"][0]
        self.assertEqual(item["tool"], "lyrics")
        self.assertEqual(item["id"], "Systran--faster-whisper-tiny")
        self.assertEqual(item["name"], "Systran/faster-whisper-tiny")
        self.assertEqual(item["role"], "recognition")
        self.assertEqual(item["fileCount"], 3)

    def test_align_model_role_is_alignment(self):
        with tempfile.TemporaryDirectory() as temp_value:
            root = Path(temp_value)
            target = root / "_tool_models" / "lyrics" / "models" / "jonatasgrosman--wav2vec2-large-xlsr-53-chinese-zh-cn"
            target.mkdir(parents=True)
            for name in ("config.json", "preprocessor_config.json", "vocab.json", "special_tokens_map.json"):
                (target / name).write_text("{}", encoding="utf-8")
            (target / "pytorch_model.bin").write_bytes(b"w" * 2048)
            registry = self._stub_registry(root)
            with mock.patch.dict("sys.modules", {
                "pymss": mock.Mock(),
                "pymss.model_registry": registry,
            }):
                summary = worker_models._storage_summary_payload(str(root))

        self.assertEqual(len(summary["toolModels"]), 1)
        self.assertEqual(summary["toolModels"][0]["role"], "alignment")

    def test_delete_tool_model_accepts_lyrics(self):
        with tempfile.TemporaryDirectory() as temp_value:
            root = Path(temp_value)
            target = root / "_tool_models" / "lyrics" / "models" / "Systran--faster-whisper-tiny"
            write_complete_tiny(target)
            registry = self._stub_registry(root)
            with mock.patch.dict("sys.modules", {
                "pymss": mock.Mock(),
                "pymss.model_registry": registry,
            }), mock.patch("worker_models.emit") as events, \
                    mock.patch("worker_models.emit_error", return_value=1) as failures:
                result = worker_models.cmd_delete_tool_model({
                    "tool": "lyrics",
                    "id": "Systran--faster-whisper-tiny",
                    "modelDir": str(root),
                })

            self.assertEqual(result, 0)
            self.assertFalse(target.exists())
            self.assertEqual(events.call_args.args[0], "tool_model_deleted")
            self.assertEqual(events.call_args.args[1]["tool"], "lyrics")
            failures.assert_not_called()

    def test_delete_tool_model_rejects_unknown_tool(self):
        with mock.patch("worker_models.emit_error", return_value=1) as failures:
            result = worker_models.cmd_delete_tool_model({
                "tool": "plugin",
                "id": "x",
                "modelDir": None,
            })
        self.assertEqual(result, 1)
        self.assertEqual(failures.call_args.args[0], "TOOL_MODEL_DELETE_FAILED")


if __name__ == "__main__":
    unittest.main()
