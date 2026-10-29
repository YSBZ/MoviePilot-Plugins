"""Optional contract suite against an explicitly supplied upstream source snapshot.

Run with MP_HOST_CONTRACT_ROOT pointing to the MoviePilot v3.1.6 source tree.
Only host infrastructure (database, notification transport) is substituted.
The upstream base class is compiled unchanged from its AST, including ABC checks.
"""
from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from abc import ABCMeta, abstractmethod

import test_plugin as adapter

HOST_ROOT = os.environ.get("MP_HOST_CONTRACT_ROOT")


class DataStore:
    def __init__(self):
        self.values = {}

    def save(self, plugin_id, key, value):
        self.values[plugin_id, key] = value

    def get_data(self, plugin_id, key=None):
        return self.values.get((plugin_id, key))


class Transport:
    def __init__(self):
        self.messages = []

    def post_message(self, message):
        self.messages.append(vars(message))


class InstanceConfig:
    def get_config_data(self, plugin_id):
        return {}


@unittest.skipUnless(HOST_ROOT, "set MP_HOST_CONTRACT_ROOT to upstream v3.1.6 source")
class Host316ContractTests(adapter.PluginAdapterTests):
    @classmethod
    def setUpClass(cls):
        source = Path(HOST_ROOT, "app/sdk/plugin/base.py").read_text()
        tree = ast.parse(source)
        base = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_PluginBase")
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), base], type_ignores=[])
        namespace = {
            "ABCMeta": ABCMeta, "abstractmethod": abstractmethod,
            "PluginDataOper": DataStore, "PluginChain": Transport,
            "SystemConfigOper": object, "PluginInstanceOper": InstanceConfig,
            "MessageHelper": object, "EventManager": object,
            "Message": lambda **kwargs: types.SimpleNamespace(**kwargs),
            "settings": types.SimpleNamespace(MP_DOMAIN=lambda path: "http://localhost/" + path),
        }
        exec(compile(ast.fix_missing_locations(module), "upstream-v3.1.6-base.py", "exec"), namespace)
        modules = adapter.make_host_modules()
        modules["app.sdk.plugin"]._PluginBase = namespace["_PluginBase"]
        cls._module_patch = patch.dict(sys.modules, modules)
        cls._module_patch.start()
        sys.modules.pop("musicdownloadbridge", None)
        cls.plugin_module = importlib.import_module("musicdownloadbridge")

    def setUp(self):
        super().setUp()
        self.plugin.sent_messages = self.plugin.chain.messages

    def test_actual_316_command_and_feishu_target_contract_keep_group_and_private_separate(self):
        import copy
        import inspect

        def load_class(path, class_name, methods, namespace):
            tree = ast.parse(Path(HOST_ROOT, path).read_text())
            original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
            body = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in methods]
            shell = ast.ClassDef(name=class_name, bases=[], keywords=[], body=body, decorator_list=[])
            module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), shell], type_ignores=[])
            exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
            return namespace[class_name]

        command = load_class("app/command.py", "Command", {"__run_command"}, {
            "copy": copy,
            "ObjectUtils": types.SimpleNamespace(arguments=lambda f: len(inspect.signature(f).parameters)),
        })()
        module = load_class("app/modules/feishu/module.py", "FeishuModule", {"_resolve_message_target"}, {})
        router = self.plugin_module.FeishuGroupRouter("oc_testgroup", b"k" * 32)
        incoming = router.parse(
            lambda **kwargs: types.SimpleNamespace(source=kwargs["source"], userid="ou_alice", is_channel_admin=True),
            "飞书音乐机器人", {"chat_id": "oc_testgroup", "chat_type": "group"}, None, {}, lambda: None,
        )
        targets = []
        def version(channel, userid, source=None):
            message = types.SimpleNamespace(channel=channel, userid=userid, source=source,
                                            original_chat_id=None, private_delivery=False, targets={})
            router.restore(message)
            targets.append(module._resolve_message_target(message))
        # Run the real host command body, which drops chat_id but preserves source.
        command._Command__run_command({"func": version}, channel="飞书", userid="ou_alice", source=incoming.source)
        command._Command__run_command({"func": version}, channel="飞书", userid="ou_alice", source="飞书音乐机器人")
        self.assertEqual(targets, [(None, "oc_testgroup", "chat_id"), ("ou_alice", None, "open_id")])

    def test_native_feishu_card_renderer_preserves_page_buttons_including_tenth_song(self):
        import json
        tree = ast.parse(Path(HOST_ROOT, "app/modules/feishu/feishu.py").read_text())
        original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Feishu")
        method = next(n for n in original.body if isinstance(n, ast.FunctionDef) and n.name == "_card_actions")
        shell = ast.ClassDef(name="Feishu", bases=[], keywords=[], body=[method], decorator_list=[])
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), shell], type_ignores=[])
        namespace = {}
        exec(compile(ast.fix_missing_locations(module), "native-card-actions.py", "exec"), namespace)
        songs = [dict(name=f"歌曲 {n}", artist="歌手") for n in range(1,11)]
        _, _, buttons = self.plugin_module.search_page("歌曲", "session123", songs, 1)
        rows = namespace["Feishu"]._card_actions(buttons)
        self.assertLessEqual(len(rows), 3)
        self.assertIn("pick:session123:10", json.dumps(rows))
        self.assertEqual(sum(len(row["columns"]) for row in rows), 6)

    def test_real_native_parser_keeps_member_role_false_and_admin_commands_blocked(self):
        import json
        class Incoming(types.SimpleNamespace):
            class MessageImage:
                @staticmethod
                def normalize_list(value):
                    return value or None
        tree=ast.parse(Path(HOST_ROOT,"app/modules/feishu/feishu.py").read_text())
        original=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="Feishu")
        methods=[n for n in original.body if isinstance(n,ast.FunctionDef) and n.name in {"parse_message","_should_reject_admin_command"}]
        shell=ast.ClassDef(name="Feishu",bases=[],keywords=[],body=methods,decorator_list=[])
        module=ast.Module(body=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0),shell],type_ignores=[])
        namespace={"json":json,"IncomingMessage":Incoming,"NotificationChannel":types.SimpleNamespace(Feishu="飞书"),
                   "matches_channel_admin":lambda channel,config,*ids:any(uid in config["FEISHU_ADMINS"].split(",") for uid in ids),
                   "logger":types.SimpleNamespace(debug=lambda *_:None)}
        exec(compile(ast.fix_missing_locations(module),"real-feishu-parser.py","exec"),namespace)
        native=namespace["Feishu"]()
        native._name="飞书音乐机器人"
        native._admins=["ou_alice"]
        native._default_open_id="ou_alice"
        native._resolve_username=lambda **kwargs:kwargs["fallback"]
        denials=[]
        native.send_text=lambda *args,**kwargs:denials.append(args)
        router=self.plugin_module.FeishuGroupRouter("oc_testgroup",b"k"*32)
        calls=[]
        def parse(command,enabled):
            return router.parse(lambda **kwargs:native.parse_message(kwargs["body"]),"飞书音乐机器人",
                dict(chat_id="oc_testgroup",chat_type="group",text=command,sender={"open_id":"ou_bob"}),None,{},
                lambda:types.SimpleNamespace(userid=None),allow_music_members=enabled,
                on_music=lambda info,cmd,args:calls.append((info,cmd,args)))
        parse("/music 夜曲",False)
        self.assertEqual(len(denials),1)
        self.assertEqual(calls,[])
        parse("/music 夜曲",True)
        self.assertEqual(calls[0][0].userid,"ou_bob")
        self.assertFalse(calls[0][0].is_channel_admin)
        parse("/restart",True)
        self.assertEqual(len(denials),2)
        self.assertEqual(len(calls),1)
