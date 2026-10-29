from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from apscheduler.triggers.interval import IntervalTrigger

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "plugins.v3"
sys.path.insert(0, str(PLUGIN_ROOT))

from http_server import MusicBridgeHTTPServer  # noqa: E402


class FakePluginBase:
    def __init__(self) -> None:
        self._test_data: dict = {}
        self.sent_messages: list[dict] = []

    def get_data(self, key=None):
        return self._test_data.get(key) if key else self._test_data

    def save_data(self, key, value):
        self._test_data[key] = value

    def post_message(self, **kwargs):
        self.sent_messages.append(kwargs)


class FakeEventManager:
    @staticmethod
    def register(_event_type):
        return lambda function: function


def make_host_modules() -> dict[str, types.ModuleType]:
    app = types.ModuleType("app")
    app.__path__ = []
    sdk = types.ModuleType("app.sdk")
    sdk.__path__ = []
    plugin = types.ModuleType("app.sdk.plugin")
    plugin._PluginBase = FakePluginBase
    events = types.ModuleType("app.sdk.events")
    events.Event = type("Event", (), {})
    events.eventmanager = FakeEventManager()
    schemas = types.ModuleType("app.schemas")
    schemas.__path__ = []
    event_types = types.ModuleType("app.schemas.types")
    event_types.EventType = type(
        "EventType", (), {"PluginAction": "plugin.action", "MessageAction": "message.action"}
    )
    return {
        "app": app,
        "app.sdk": sdk,
        "app.sdk.plugin": plugin,
        "app.sdk.events": events,
        "app.schemas": schemas,
        "app.schemas.types": event_types,
    }


class PluginAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._module_patch = patch.dict(sys.modules, make_host_modules())
        cls._module_patch.start()
        cls.plugin_module = importlib.import_module("musicdownloadbridge")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("musicdownloadbridge", None)
        cls._module_patch.stop()

    def setUp(self) -> None:
        self.server = MusicBridgeHTTPServer().start()
        self.addCleanup(self.server.close)
        self.plugin = self.plugin_module.MusicDownloadBridge()
        self.plugin.init_plugin(
            {
                "enabled": True,
                "base_url": self.server.base_url,
                "token": "local-test-token",
                "sources": "netease",
                "poll_interval": 10,
            }
        )
        self.identity = {"channel": "feishu", "source": "local-chat", "user": "alice"}

    def test_group_module_hooks_preserve_user_and_restore_reply_chat(self) -> None:
        config = {
            "enabled": True, "base_url": self.server.base_url,
            "token": "local-test-token", "feishu_group_id": "oc_testgroup",
        }
        self.plugin.init_plugin(config)
        def native_parser(**kwargs):
            return types.SimpleNamespace(
                source=kwargs["source"], text=kwargs["body"]["text"],
                userid="ou_alice", is_channel_admin=True,
            )
        native = types.SimpleNamespace(message_parser=native_parser)
        self.plugin.chain = types.SimpleNamespace(modulemanager=types.SimpleNamespace(get_running_module=lambda _: native))
        hooks = self.plugin.get_module()
        incoming = hooks["message_parser"](
            "飞书音乐机器人", {"chat_id": "oc_testgroup", "chat_type": "group", "text": "@_user_1 /version"}, None, {},
        )
        self.assertEqual(incoming.text, "/version")
        self.assertEqual(incoming.userid, "ou_alice")
        message = types.SimpleNamespace(source=incoming.source, channel="飞书", userid=incoming.userid, original_chat_id=None, private_delivery=False)
        hooks["post_message"](message)
        self.assertEqual(message.original_chat_id, "oc_testgroup")
        self.assertEqual(message.source, "飞书音乐机器人")
        self.assertEqual(message.userid, "ou_alice")
        # Reconfiguration/restart retains the same route signing key.
        self.plugin.init_plugin(config)
        again = self.plugin.get_module()["message_parser"](
            "飞书音乐机器人", {"chat_id": "oc_testgroup", "chat_type": "group", "text": "/version"}, None, {},
        )
        self.assertEqual(again.source, incoming.source)

    def tearDown(self) -> None:
        self.plugin.stop_service()

    def event(self, action: str, arg_str: str):
        return types.SimpleNamespace(
            event_data={
                "action": action,
                "arg_str": arg_str,
                **self.identity,
            }
        )

    def test_commands_use_local_http_api_and_reply_on_originating_feishu_route(self) -> None:
        commands = self.plugin.get_command()
        self.assertEqual({item["cmd"] for item in commands}, {"/music", "/music_pick", "/music_status"})

        self.plugin.on_plugin_action(self.event("music_search", "夜曲"))
        search_message = self.plugin.sent_messages[-1]
        self.assertEqual(search_message["channel"], "feishu")
        self.assertEqual(search_message["source"], "local-chat")
        self.assertEqual(search_message["userid"], "alice")
        callback = search_message["buttons"][0][0]["callback_data"]
        self.assertTrue(callback.startswith("[PLUGIN]MusicDownloadBridge|pick:"))
        session_id = callback.split("pick:", 1)[1].rsplit(":", 1)[0]
        self.assertIn(f"/music_pick {session_id} ", search_message["text"])

        self.plugin.on_plugin_action(self.event("music_pick", f"{session_id} 1"))
        picked = self.plugin.sent_messages[-1]
        self.assertEqual(picked["channel"], "feishu")
        self.assertEqual(picked["source"], "local-chat")
        self.assertEqual(picked["userid"], "alice")
        self.assertEqual(picked["title"], "已提交下载")
        posts = [item for item in self.server.state["requests"] if item["method"] == "POST"]
        self.assertEqual(len(posts), 1)

        request_id = posts[0]["json"]["request_id"]
        self.server.set_status(
            request_id,
            "success",
            path="/shared/download/周杰伦 - 夜曲.flac",
        )
        self.plugin.on_plugin_action(self.event("music_status", request_id))
        status_message = self.plugin.sent_messages[-1]
        self.assertEqual(status_message["source"], "local-chat")
        self.assertIn("/shared/download/周杰伦 - 夜曲.flac", status_message["text"])
        self.assertIn("尚未确认已入库", status_message["text"])

    def test_message_action_is_scoped_to_plugin_and_full_session_identity(self) -> None:
        self.plugin.on_plugin_action(self.event("music_search", "夜曲"))
        callback = self.plugin.sent_messages[-1]["buttons"][0][0]["callback_data"]
        callback_text = callback.split("|", 1)[1]
        before = len([item for item in self.server.state["requests"] if item["method"] == "POST"])

        self.plugin.on_message_action(
            types.SimpleNamespace(
                event_data={
                    "plugin_id": "MusicDownloadBridge",
                    "text": callback_text,
                    "userid": "alice",
                    "channel": "feishu",
                    "source": "other-chat",
                }
            )
        )
        after_wrong_source = len(
            [item for item in self.server.state["requests"] if item["method"] == "POST"]
        )
        self.assertEqual(after_wrong_source, before)

        self.plugin.on_message_action(
            types.SimpleNamespace(
                event_data={
                    "plugin_id": "MusicDownloadBridge",
                    "text": callback_text,
                    "userid": "alice",
                    "channel": "feishu",
                    "source": "local-chat",
                }
            )
        )
        after_valid_source = len(
            [item for item in self.server.state["requests"] if item["method"] == "POST"]
        )
        self.assertEqual(after_valid_source, before + 1)

    def test_service_registration_uses_host_interval_and_nested_payload_remains_compatible(self) -> None:
        registered = self.plugin.get_service()
        self.assertEqual(registered[0]["kwargs"], {"seconds": 10})
        trigger = IntervalTrigger(**registered[0]["kwargs"])
        self.assertEqual(trigger.interval.total_seconds(), 10)

        event = types.SimpleNamespace(
            event_data={"data": {"action": "music_search", "arg_str": "夜曲", **self.identity}}
        )
        self.plugin.on_plugin_action(event)
        self.assertIn("音乐搜索结果", self.plugin.sent_messages[-1]["title"])

    def test_invalid_configuration_and_repeated_stop_are_safe(self) -> None:
        self.plugin.init_plugin({"enabled": True, "base_url": self.server.base_url})
        self.assertFalse(self.plugin.get_state())
        self.assertEqual(self.plugin.get_service(), [])

        config = {
            "enabled": True,
            "base_url": self.server.base_url,
            "token": "local-test-token",
        }
        self.plugin.init_plugin(config)
        self.assertTrue(self.plugin.get_state())
        self.plugin.stop_service()
        self.plugin.stop_service()
        self.assertFalse(self.plugin.get_state())
        self.assertEqual(self.plugin.get_service(), [])

        self.plugin.init_plugin(config)
        self.assertTrue(self.plugin.get_state())

    def test_poll_service_notifies_saved_owner_once_after_download_success(self) -> None:
        self.plugin.on_plugin_action(self.event("music_search", "夜曲"))
        callback = self.plugin.sent_messages[-1]["buttons"][0][0]["callback_data"]
        session_id = callback.split("pick:", 1)[1].rsplit(":", 1)[0]
        self.plugin.on_plugin_action(self.event("music_pick", f"{session_id} 1"))
        request_id = next(
            item["json"]["request_id"]
            for item in self.server.state["requests"]
            if item["method"] == "POST"
        )
        self.server.set_status(request_id, "success", path="/shared/download/song.flac")

        # A status query may persist the terminal state before notification.
        self.plugin._service.get_request(request_id, self.identity)
        self.plugin.init_plugin({"enabled": True, "base_url": self.server.base_url,
                                 "token": "local-test-token", "poll_interval": 10})
        before_poll = len(self.plugin.sent_messages)
        self.plugin.poll_service()
        after_first_poll = len(self.plugin.sent_messages)
        self.plugin.poll_service()

        self.assertEqual(after_first_poll, before_poll + 1)
        self.assertEqual(len(self.plugin.sent_messages), after_first_poll)
        notification = self.plugin.sent_messages[-1]
        self.assertEqual(notification["title"], "音乐下载完成")
        self.assertEqual(notification["userid"], "alice")
        self.assertEqual(notification["source"], "local-chat")
        self.assertIn("尚未确认已入库", notification["text"])

    def test_another_user_cannot_read_status_or_receive_saved_path(self) -> None:
        self.plugin.on_plugin_action(self.event("music_search", "夜曲"))
        callback = self.plugin.sent_messages[-1]["buttons"][0][0]["callback_data"]
        session_id = callback.split("pick:", 1)[1].rsplit(":", 1)[0]
        self.plugin.on_plugin_action(self.event("music_pick", f"{session_id} 1"))
        request_id = next(
            item["json"]["request_id"]
            for item in self.server.state["requests"]
            if item["method"] == "POST"
        )
        self.server.set_status(request_id, "completed", path="/private/alice.flac")

        event = types.SimpleNamespace(
            event_data={
                "action": "music_status",
                "arg_str": request_id,
                "channel": "feishu",
                "source": "local-chat",
                "user": "bob",
            }
        )
        self.plugin.on_plugin_action(event)

        reply = self.plugin.sent_messages[-1]
        self.assertIn("未找到", reply["text"])
        self.assertNotIn("/private/alice.flac", reply["text"])
        self.assertEqual(reply["userid"], "bob")


if __name__ == "__main__":
    unittest.main()
