"""MoviePilot V3 adapter for the music download bridge service."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from typing import Any
from types import SimpleNamespace

from app.schemas.types import EventType
from app.sdk.events import Event, eventmanager
from app.sdk.plugin import _PluginBase

from .core import BridgeError, MusicBridgeClient, MusicDownloadService
from .feishu_routing import FeishuGroupRouter
from .presentation import search_page, status_text

DEFAULT_SOURCES = ("kugou", "migu", "qq", "netease")
SOURCE_ITEMS = [
    {"title": "酷狗音乐", "value": "kugou"},
    {"title": "咪咕音乐", "value": "migu"},
    {"title": "QQ 音乐", "value": "qq"},
    {"title": "网易云音乐", "value": "netease"},
]

class MusicDownloadBridge(_PluginBase):
    """Search and submit one music candidate through a bounded HTTP bridge."""

    plugin_name = "音乐下载桥接"
    plugin_desc = "通过消息搜索歌曲并提交下载，完成后由 MoviePilot 继续整理。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/163music_A.png"
    plugin_version = "1.0.6"
    plugin_author = "ys"
    author_url = ""
    plugin_config_prefix = "musicdownloadbridge_"
    plugin_order = 50
    auth_level = 1

    _STATE_KEY = "music_bridge_state_v1"
    _SESSION_TTL_SECONDS = 15 * 60

    def __init__(self) -> None:
        super().__init__()
        self._enabled = False
        self._stopped = True
        self._sources: list[str] = []
        self._poll_interval = 20
        self._client: MusicBridgeClient | None = None
        self._service: MusicDownloadService | None = None
        self._configuration_error: str | None = None
        self._group_router: FeishuGroupRouter | None = None
        self._group_members_music = False

    def init_plugin(self, config: dict[str, Any] | None = None) -> None:
        """Apply current settings; safe for repeated initialization/reload."""
        self._close_client()
        if config is None:
            config = self.get_config() or {}
        config = dict(config or {})

        self._enabled = bool(config.get("enabled"))
        self._stopped = False
        self._sources = self._parse_sources(config.get("sources"))
        # VSelect expects an array. Preserve all other saved settings while
        # migrating the former comma-separated text field once.
        if not isinstance(config.get("sources"), list):
            config["sources"] = list(self._sources)
            self.update_config(config)
        self._poll_interval = self._bounded_int(config.get("poll_interval"), 20, 5, 60)
        self._configuration_error = None
        self._group_router = None
        self._group_members_music = config.get("group_members_music") is True
        self._service = None
        self._client = None

        if not self._enabled:
            return
        try:
            self._client = MusicBridgeClient(
                str(config.get("base_url") or ""),
                str(config.get("token") or ""),
            )
        except ValueError as error:
            self._enabled = False
            self._configuration_error = str(error)
            return

        self._service = MusicDownloadService(
            self._client,
            lambda: self.get_data(self._STATE_KEY) or {},
            lambda state: self.save_data(self._STATE_KEY, state),
            session_ttl_seconds=self._SESSION_TTL_SECONDS,
            poll_batch_size=5,
        )

        group_id = str(config.get("feishu_group_id") or "").strip()
        if group_id:
            key = self.get_data("music_group_route_key_v1")
            if not isinstance(key, str) or len(key) != 64:
                key = secrets.token_hex(32)
                self.save_data("music_group_route_key_v1", key)
            try:
                self._group_router = FeishuGroupRouter(group_id, bytes.fromhex(key))
            except ValueError:
                self._configuration_error = "飞书群 Chat ID 无效，群路由适配未启用"

    def get_module(self) -> dict[str, Any]:
        """Use documented MP module hooks; no monkey patches to host code."""
        if not self.get_state() or not self._group_router:
            return {}
        return {
            "message_parser": self._group_message_parser,
            "post_message": self._group_post_message,
            "post_medias_message": self._group_post_message,
            "post_torrents_message": self._group_post_message,
        }

    def _group_message_parser(self, source: str, body: Any, form: Any, args: Any) -> Any:
        if not self.get_state() or not self._group_router:
            return None
        module = self.chain.modulemanager.get_running_module("FeishuModule")
        if module is None:
            return None
        def rejected() -> Any:
            from app.schemas.message import IncomingMessage
            return IncomingMessage()
        return self._group_router.parse(
            module.message_parser, source, body, form, args, rejected,
            allow_music_members=self._group_members_music, on_music=self._dispatch_group_music,
        )

    def _dispatch_group_music(self, incoming: Any, command: str, arguments: str) -> None:
        actions = {"/music": "music_search", "/music_pick": "music_pick", "/music_status": "music_status"}
        action = actions.get(command)
        if action is None or not self.get_state():
            return
        self.on_plugin_action(SimpleNamespace(event_data={
            "plugin_id": self.__class__.__name__, "action": action, "arg_str": arguments,
            "user": incoming.userid, "source": incoming.source, "channel": incoming.channel,
        }))

    def _group_post_message(self, message: Any, *args: Any, **kwargs: Any) -> None:
        if self.get_state() and self._group_router:
            # The host still sends the message once, with its original channel
            # rules and formatting, after restoring source and group context.
            self._group_router.restore(message)

    def get_state(self) -> bool:
        return self._enabled and not self._stopped and self._service is not None

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        """Register explicit commands as the supported-channel fallback."""
        return [
            {
                "cmd": "/music",
                "event": EventType.PluginAction,
                "desc": "搜索歌曲",
                "category": "音乐",
                "data": {"action": "music_search", "plugin_id": "MusicDownloadBridge"},
            },
            {
                "cmd": "/music_pick",
                "event": EventType.PluginAction,
                "desc": "选择歌曲并下载",
                "category": "音乐",
                "data": {"action": "music_pick", "plugin_id": "MusicDownloadBridge"},
            },
            {
                "cmd": "/music_status",
                "event": EventType.PluginAction,
                "desc": "查询音乐下载状态",
                "category": "音乐",
                "data": {"action": "music_status", "plugin_id": "MusicDownloadBridge"},
            },
        ]

    def get_api(self) -> list[dict[str, Any]]:
        return []

    def get_form(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        def field(model: str, label: str, **props: Any) -> dict[str, Any]:
            return {
                "component": "VTextField",
                "props": {
                    "model": model, "label": label,
                    "variant": "outlined", "density": "comfortable",
                    "persistentHint": True, **props,
                },
            }

        def column(component: dict[str, Any], md: int = 12) -> dict[str, Any]:
            return {
                "component": "VCol",
                "props": {"cols": 12, "md": md, "class": "pb-4"},
                "content": [component],
            }

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [column({
                            "component": "VSwitch",
                            "props": {"model": "enabled", "label": "启用插件", "hideDetails": True},
                        })],
                    },
                    {
                        "component": "VRow",
                        "content": [column(field(
                            "base_url", "音乐下载服务地址",
                            hint="保留 /music 前缀，例如 http://NAS-IP:8085/music",
                            placeholder="http://NAS-IP:8085/music",
                        ))],
                    },
                    {
                        "component": "VRow",
                        "content": [column(field(
                            "token", "Bearer token", type="password",
                            autocomplete="new-password",
                            hint="与下载服务的 MUSIC_BRIDGE_TOKEN 保持一致",
                        ))],
                    },
                    {
                        "component": "VRow",
                        "content": [column(field(
                            "feishu_group_id", "飞书群 Chat ID（可选）",
                            placeholder="oc_...",
                            hint="只适配此群的命令与音乐回复；私聊保持私聊。留空关闭群路由适配。",
                        ))],
                    },
                    {
                        "component": "VRow",
                        "content": [column({
                            "component": "VSwitch",
                            "props": {
                                "model": "group_members_music", "label": "允许此群成员点歌",
                                "hint": "仅开放三条音乐命令；管理命令仍使用 MP 管理员名单。",
                                "persistentHint": True,
                            },
                        })],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            column({
                                "component": "VSelect",
                                "props": {
                                    "model": "sources", "label": "搜索音源",
                                    "items": SOURCE_ITEMS,
                                    "multiple": True, "chips": True,
                                    "closableChips": True, "clearable": True,
                                    "variant": "outlined", "density": "comfortable",
                                    "hint": "默认四源，可按需取消；清空后仍使用默认四源。需配套 DL 1.0.1 或更新版本。",
                                    "persistentHint": True,
                                },
                            }, md=6),
                            column(field(
                                "poll_interval", "下载状态轮询间隔（秒）", type="number",
                                min=5, max=60, step=5,
                                hint="5–60 秒，建议 20 秒",
                            ), md=6),
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "base_url": "",
            "token": "",
            "sources": list(DEFAULT_SOURCES),
            "poll_interval": 20,
            "feishu_group_id": "",
            "group_members_music": False,
        }

    def get_page(self) -> list[dict[str, Any]]:
        return []

    def get_service(self) -> list[dict[str, Any]]:
        if not self.get_state():
            return []
        return [
            {
                "id": f"{self.__class__.__name__}Poll",
                "name": "音乐下载状态轮询",
                "trigger": "interval",
                "func": self.poll_service,
                "kwargs": {"seconds": self._poll_interval},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def on_plugin_action(self, event: Event) -> None:
        """Handle /music, /music_pick and /music_status command actions."""
        if not self.get_state() or not event:
            return
        outer = self._mapping(getattr(event, "event_data", None))
        nested = self._mapping(outer.get("data"))
        data = {**outer, **nested}
        target_plugin = data.get("plugin_id")
        if target_plugin and target_plugin != self.__class__.__name__:
            return

        action = str(data.get("action") or "")
        arg_str = str(data.get("arg_str") or "").strip()
        identity = self._identity(data)
        if action == "music_search":
            self._handle_search(arg_str, identity)
        elif action == "music_pick":
            self._handle_pick(arg_str, identity)
        elif action == "music_status":
            self._handle_status(arg_str, identity)

    @eventmanager.register(EventType.MessageAction)
    def on_message_action(self, event: Event) -> None:
        """Handle optional [PLUGIN] choice callbacks with the same ACL checks."""
        if not self.get_state() or not event:
            return
        data = self._mapping(getattr(event, "event_data", None))
        if data.get("plugin_id") != self.__class__.__name__:
            return
        callback = str(data.get("text") or "")
        if not callback.startswith(("pick:", "page:", "status:")):
            return
        identity = self._identity(
            {
                "channel": data.get("channel"),
                "source": data.get("source"),
                "user": data.get("userid"),
            }
        )
        if callback.startswith("page:"):
            self._handle_page(callback.removeprefix("page:"), identity, event_data=data)
        elif callback.startswith("status:"):
            self._handle_status(callback.removeprefix("status:"), identity)
        else:
            self._handle_pick(callback.removeprefix("pick:"), identity, callback=True)

    def poll_service(self) -> None:
        """Poll a bounded batch; downloads themselves remain in the Go service."""
        if not self.get_state() or not self._service:
            return
        self._service.poll_pending()
        for request in self._service.pending_notifications():
            if request["status"] not in {"completed", "failed", "skipped", "canceled"} or request.get("notified"):
                continue
            notification = self._service.notification_target(request["request_id"])
            if not notification:
                continue
            identity = notification["identity"]
            self._reply(
                identity,
                self._notification_title(request["status"]),
                self._format_status(request),
                buttons=self._status_buttons(request),
            )
            self._service.mark_notified(request["request_id"])

    def stop_service(self) -> None:
        """Release the HTTP client safely during plugin reload or shutdown."""
        self._stopped = True
        self._enabled = False
        self._group_router = None
        self._close_client()
        self._service = None

    def _handle_search(self, query: str, identity: dict[str, Any]) -> None:
        if not query:
            self._reply(identity, "音乐搜索", "用法：/music 歌名或歌手")
            return
        if not self._complete_identity(identity):
            return
        try:
            result = self._service.search(query, self._sources, 10, identity)
        except BridgeError as error:
            self._reply(identity, "音乐搜索失败", str(error))
            return

        songs = result["songs"]
        session_id = result["session_id"]
        if not songs:
            self._reply(identity, "音乐搜索", "没有找到候选歌曲，请换个关键词再试。")
            return

        title, text, buttons = search_page(query, session_id, songs)
        self._reply(identity, title, text, buttons=buttons)

    def _handle_page(self, arg_str: str, identity: dict[str, Any], *, event_data: Mapping[str, Any] | None = None) -> None:
        try:
            session_id, number = arg_str.split(":")
            result = self._service.search_session(session_id, identity)
            title, text, buttons = search_page(result["query"], session_id, result["songs"], int(number))
        except (BridgeError, ValueError):
            self._reply(identity, "音乐搜索", "结果已过期、页码无效或不可访问，请重新搜索。")
            return
        if not self._edit_search_card(identity, title, text, buttons, event_data or {}):
            self._reply(identity, title, text, buttons=buttons)

    def _edit_search_card(self, identity: Mapping[str, Any], title: str, text: str,
                          buttons: list[list[dict[str, str]]], event_data: Mapping[str, Any]) -> bool:
        channel = identity.get("channel")
        message_id = event_data.get("original_message_id")
        chat_id = event_data.get("original_chat_id")
        if str(getattr(channel, "value", channel)).lower() not in ("feishu", "飞书") or not message_id or not chat_id:
            return False
        edit = getattr(getattr(self, "chain", None), "edit_message", None)
        if not callable(edit):
            return False
        route = SimpleNamespace(source=identity.get("source"), channel=channel,
                                original_chat_id=chat_id, private_delivery=False)
        if self._group_router:
            self._group_router.restore(route)
        try:
            return bool(edit(channel=channel, source=route.source, message_id=message_id,
                             chat_id=route.original_chat_id, title=title, text=text, buttons=buttons))
        except Exception:
            # A failed edit must not make the remaining candidates inaccessible.
            return False

    def _handle_pick(
        self,
        arg_str: str,
        identity: dict[str, Any],
        *,
        callback: bool = False,
    ) -> None:
        parts = arg_str.split(":") if callback else arg_str.split()
        if len(parts) != 2:
            self._reply(identity, "选择歌曲", "用法：/music_pick 会话ID 编号")
            return
        try:
            request = self._service.select(parts[0], int(parts[1]), identity)
        except (BridgeError, ValueError) as error:
            self._reply(identity, "选择歌曲失败", str(error) or "候选编号无效")
            return
        if request["status"] == "completed":
            title = "下载已完成"
        elif request["status"] == "failed":
            title = "下载提交失败"
        else:
            title = "已提交下载"
        self._reply(identity, title, self._format_status(request), buttons=self._status_buttons(request))

    def _handle_status(self, request_id: str, identity: dict[str, Any]) -> None:
        request_id = request_id.strip()
        if not request_id or len(request_id.split()) != 1:
            self._reply(identity, "音乐下载状态", "用法：/music_status 请求ID")
            return
        try:
            request = self._service.get_request(request_id, identity)
        except BridgeError as error:
            self._reply(identity, "音乐下载状态", str(error))
            return
        self._reply(identity, "音乐下载状态", self._format_status(request, detailed=True))

    def _format_status(self, request: Mapping[str, Any], *, detailed: bool = False) -> str:
        return status_text(request, detailed=detailed)

    @staticmethod
    def _status_buttons(request: Mapping[str, Any]) -> list[list[dict[str, str]]]:
        request_id = str(request.get("request_id") or "")
        return [[{"text": "查看详情", "callback_data": f"[PLUGIN]MusicDownloadBridge|status:{request_id}"}]]

    @staticmethod
    def _notification_title(status: str) -> str:
        return {
            "completed": "音乐下载完成",
            "skipped": "音乐下载已跳过",
            "canceled": "音乐下载已取消",
        }.get(status, "音乐下载失败")

    def _reply(
        self,
        identity: Mapping[str, Any],
        title: str,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> None:
        if not self._complete_identity(identity):
            return
        self.post_message(
            channel=identity["channel"],
            source=identity["source"],
            title=title,
            text=text,
            userid=identity["user"],
            buttons=buttons,
        )

    @staticmethod
    def _identity(data: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "user": data.get("user") if data.get("user") is not None else data.get("userid"),
            "source": data.get("source"),
            "channel": data.get("channel"),
        }

    @staticmethod
    def _complete_identity(identity: Mapping[str, Any]) -> bool:
        return all(
            value is not None and str(getattr(value, "value", value)).strip()
            for value in (identity.get("user"), identity.get("source"), identity.get("channel"))
        )

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "model_dump"):
            result = value.model_dump()
            return dict(result) if isinstance(result, Mapping) else {}
        return {}

    @staticmethod
    def _parse_sources(value: Any) -> list[str]:
        if isinstance(value, str):
            values: Sequence[Any] = value.split(",")
        elif isinstance(value, Sequence):
            values = value
        else:
            values = []
        return list(dict.fromkeys(str(source).strip() for source in values if str(source).strip())) or list(DEFAULT_SOURCES)

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(parsed, maximum))

    @staticmethod
    def _display(value: Any) -> str:
        return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:120]

    def _close_client(self) -> None:
        if self._client:
            self._client.close()
        self._client = None
