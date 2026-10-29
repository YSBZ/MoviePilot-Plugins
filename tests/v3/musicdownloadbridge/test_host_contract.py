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
