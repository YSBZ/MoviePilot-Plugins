from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_PACKAGE = Path(__file__).resolve().parents[3] / "plugins.v3" / "musicdownloadbridge"
sys.path.insert(0, str(PLUGIN_PACKAGE))

from core import (  # noqa: E402
    BridgeError,
    BridgeHTTPError,
    MusicBridgeClient,
    MusicDownloadService,
    SessionUnavailable,
)
from http_server import MusicBridgeHTTPServer  # noqa: E402


IDENTITY = {"user": "alice", "source": "feishu", "channel": "feishu"}
OTHER_IDENTITY = {"user": "bob", "source": "feishu", "channel": "feishu"}


class MemoryStore:
    def __init__(self) -> None:
        self.value: dict = {}

    def load(self) -> dict:
        return self.value

    def save(self, value: dict) -> None:
        self.value = value


class MusicDownloadServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = MusicBridgeHTTPServer().start()
        self.addCleanup(self.server.close)
        self.store = MemoryStore()
        self.clock = [1_000.0]
        self.client = MusicBridgeClient(
            self.server.base_url,
            "local-test-token",
            timeout=(0.5, 0.5),
        )
        self.addCleanup(self.client.close)
        self.service = self.make_service()

    def make_service(self) -> MusicDownloadService:
        return MusicDownloadService(
            self.client,
            self.store.load,
            self.store.save,
            now=lambda: self.clock[0],
            session_ttl_seconds=900,
            request_retention_seconds=86_400,
        )

    def test_real_http_search_pick_is_idempotent_and_status_survives_restart(self) -> None:
        result = self.service.search("夜曲", ["netease"], 5, IDENTITY)

        self.assertEqual(result["songs"][0]["name"], "夜曲")
        self.assertTrue(result["session_id"])
        search = self.server.state["requests"][0]
        self.assertEqual(search["path"], "/music/api/music-bridge/search")
        self.assertEqual(search["authorization"], "Bearer local-test-token")
        self.assertEqual(search["query"], {"q": ["夜曲"], "sources": ["netease"], "limit": ["5"]})

        first = self.service.select(result["session_id"], 1, IDENTITY)
        duplicate = self.service.select(result["session_id"], 1, IDENTITY)

        self.assertEqual(first["request_id"], duplicate["request_id"])
        submissions = [item for item in self.server.state["requests"] if item["method"] == "POST"]
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0]["json"]["request_id"], first["request_id"])
        self.assertEqual(submissions[0]["json"]["song"]["id"], "song-001")

        self.server.set_status(
            first["request_id"],
            "success",
            path="/shared/download/周杰伦 - 夜曲.flac",
            warning="MP 后续监控整理",
        )
        restarted = self.make_service()
        changed = restarted.poll_pending()
        visible = restarted.get_request(first["request_id"], IDENTITY)
        self.assertEqual(changed[0]["status"], "completed")
        self.assertEqual(visible["path"], "/shared/download/周杰伦 - 夜曲.flac")
        self.assertEqual(visible["warning"], "MP 后续监控整理")

    def test_user_identity_and_session_expiry_do_not_expose_another_users_request(self) -> None:
        result = self.service.search("夜曲", [], 5, IDENTITY)
        submitted = self.service.select(result["session_id"], 1, IDENTITY)

        with self.assertRaises(SessionUnavailable):
            self.service.select(result["session_id"], 1, OTHER_IDENTITY)
        with self.assertRaises(SessionUnavailable):
            self.service.get_request(submitted["request_id"], OTHER_IDENTITY)
        for wrong_route in (
            {"user": "alice", "source": "other-chat", "channel": "feishu"},
            {"user": "alice", "source": "feishu", "channel": "telegram"},
        ):
            with self.subTest(identity=wrong_route):
                with self.assertRaises(SessionUnavailable):
                    self.service.get_request(submitted["request_id"], wrong_route)

        self.server.set_status(
            submitted["request_id"],
            "completed",
            path="/private/alice.flac",
        )
        self.clock[0] += 901
        with self.assertRaises(SessionUnavailable):
            self.service.select(result["session_id"], 1, IDENTITY)
        self.service.poll_pending()
        self.assertEqual(
            self.service.get_request(submitted["request_id"], IDENTITY)["path"],
            "/private/alice.flac",
        )

    def test_failed_download_can_be_retried_but_duplicate_selection_stays_single(self) -> None:
        result = self.service.search("夜曲", [], 5, IDENTITY)
        failed = self.service.select(result["session_id"], 1, IDENTITY)
        self.server.set_status(failed["request_id"], "failed", error="音源临时错误")
        self.service.poll_pending()

        retry = self.service.select(result["session_id"], 1, IDENTITY)
        duplicate_retry = self.service.select(result["session_id"], 1, IDENTITY)

        self.assertNotEqual(failed["request_id"], retry["request_id"])
        self.assertEqual(retry["request_id"], duplicate_retry["request_id"])
        submissions = [item for item in self.server.state["requests"] if item["method"] == "POST"]
        self.assertEqual(len(submissions), 2)

    def test_post_timeout_state_recovers_after_restart_with_same_idempotency_key(self) -> None:
        result = self.service.search("夜曲", [], 5, IDENTITY)
        self.server.state["fail_post_status"] = 503
        uncertain = self.service.select(result["session_id"], 1, IDENTITY)
        self.assertEqual(uncertain["status"], "unknown")

        self.server.state["fail_post_status"] = None
        restarted = self.make_service()
        recovered = restarted.poll_pending()

        self.assertEqual(recovered[0]["request_id"], uncertain["request_id"])
        self.assertEqual(recovered[0]["status"], "waiting")
        post_ids = [
            item["json"]["request_id"]
            for item in self.server.state["requests"]
            if item["method"] == "POST"
        ]
        self.assertEqual(post_ids, [uncertain["request_id"], uncertain["request_id"]])

    def test_post_404_is_permanent_and_does_not_retry(self) -> None:
        result = self.service.search("夜曲", [], 5, IDENTITY)
        self.server.state["fail_post_status"] = 404
        request = self.service.select(result["session_id"], 1, IDENTITY)
        self.assertEqual(request["status"], "failed")
        before = len(self.server.state["requests"])
        self.service.poll_pending()
        self.assertEqual(len(self.server.state["requests"]), before)

    def test_http_errors_do_not_echo_bearer_token(self) -> None:
        self.server.state["fail_search_status"] = 503

        with self.assertRaises(BridgeHTTPError) as raised:
            self.service.search("夜曲", [], 5, IDENTITY)

        self.assertNotIn("local-test-token", str(raised.exception))

    def test_session_and_request_state_have_configurable_hard_limits(self) -> None:
        capped = MusicDownloadService(
            self.client,
            self.store.load,
            self.store.save,
            now=lambda: self.clock[0],
            max_sessions=2,
            max_requests=1,
        )
        first = capped.search("first", [], 5, IDENTITY)
        second = capped.search("second", [], 5, IDENTITY)
        third = capped.search("third", [], 5, IDENTITY)

        self.assertLessEqual(len(self.store.load()["sessions"]), 2)
        with self.assertRaises(SessionUnavailable):
            capped.select(first["session_id"], 1, IDENTITY)
        capped.select(second["session_id"], 1, IDENTITY)
        with self.assertRaises(BridgeError):
            capped.select(third["session_id"], 1, IDENTITY)
        self.assertLessEqual(len(self.store.load()["requests"]), 1)

    def test_go_service_status_vocabulary_is_preserved_or_normalized_safely(self) -> None:
        cases = [
            ("waiting", "waiting"),
            ("downloading", "downloading"),
            ("success", "completed"),
            ("skipped", "skipped"),
            ("canceled", "canceled"),
        ]
        for remote_status, expected_status in cases:
            with self.subTest(remote_status=remote_status):
                result = self.service.search(remote_status, [], 5, IDENTITY)
                request = self.service.select(result["session_id"], 1, IDENTITY)
                self.server.set_status(request["request_id"], remote_status)
                polled = self.service.poll_pending()
                current = next(item for item in polled if item["request_id"] == request["request_id"])
                self.assertEqual(current["status"], expected_status)


if __name__ == "__main__":
    unittest.main()
