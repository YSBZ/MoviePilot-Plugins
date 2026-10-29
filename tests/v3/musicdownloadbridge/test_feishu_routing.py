from __future__ import annotations
import sys
from pathlib import Path
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'plugins.v3/musicdownloadbridge'))
from feishu_routing import FeishuGroupRouter


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.router = FeishuGroupRouter('oc_testgroup', b'k' * 32)
        self.seen = []

    def native(self, **kwargs):
        self.seen.append(kwargs)
        body = kwargs['body']
        return types.SimpleNamespace(userid=body['sender']['open_id'], source=kwargs['source'],
                                     text=body.get('text', ''), chat_id=body['chat_id'], is_channel_admin=True)

    def parse(self, text='/version', **updates):
        body = {'chat_id': 'oc_testgroup', 'chat_type': 'group', 'text': text,
                'sender': {'open_id': 'ou_alice'}}
        body.update(updates)
        return self.router.parse(self.native, '飞书音乐机器人', body, None, {}, lambda: types.SimpleNamespace(userid=None))

    def message(self, source, **updates):
        data = dict(source=source, userid='ou_alice', channel='飞书', original_chat_id=None, private_delivery=False)
        data.update(updates)
        return types.SimpleNamespace(**data)

    def test_group_mention_normalized_before_native_admin_validation(self):
        result = self.parse('@_user_1 /version')
        self.assertEqual(self.seen[0]['body']['text'], '/version')
        self.assertEqual(result.userid, 'ou_alice')
        self.assertTrue(result.is_channel_admin)

    def test_dropped_chat_context_recovered_from_source_without_changing_user(self):
        incoming = self.parse()
        message = self.message(incoming.source)
        self.assertTrue(self.router.restore(message))
        self.assertEqual(message.original_chat_id, 'oc_testgroup')
        self.assertEqual(message.userid, 'ou_alice')
        self.assertEqual(message.source, '飞书音乐机器人')

    def test_private_reply_not_redirected_after_same_user_group_message(self):
        group = self.parse()
        private = self.message('飞书音乐机器人')
        self.assertFalse(self.router.restore(private))
        self.assertIsNone(private.original_chat_id)
        message = self.message(group.source)
        self.router.restore(message)
        self.assertEqual(message.original_chat_id, 'oc_testgroup')

    def test_foreign_groups_and_private_input_left_to_native_host(self):
        self.assertIsNone(self.parse(chat_id='oc_other'))
        self.assertIsNone(self.parse(chat_type='p2p'))
        self.assertFalse(self.seen)

    def test_forged_route_and_changed_group_key_are_rejected(self):
        source = self.parse().source
        forged = self.message(source[:-1] + ('0' if source[-1] != '0' else '1'))
        self.assertFalse(self.router.restore(forged))
        self.assertFalse(FeishuGroupRouter('oc_other', b'k' * 32).restore(self.message(source)))
        self.assertFalse(FeishuGroupRouter('oc_testgroup', b'x' * 32).restore(self.message(source)))

    def test_routes_survive_restart_and_callback_keeps_group_session(self):
        result = self.parse()
        again = self.parse(type='cardAction', chat_type=None, callback_data='[PLUGIN]MusicDownloadBridge|pick:id:1')
        self.assertEqual(result.source, again.source)
        restarted = FeishuGroupRouter('oc_testgroup', b'k' * 32)
        message = self.message(result.source)
        self.assertTrue(restarted.restore(message))

    def test_explicit_private_delivery_remains_private(self):
        message = self.message(self.parse().source, private_delivery=True)
        self.router.restore(message)
        self.assertIsNone(message.original_chat_id)
        self.assertEqual(message.userid, 'ou_alice')

    def test_native_rejection_is_terminal_and_does_not_fall_back(self):
        rejected = self.router.parse(lambda **_: None, 'base', {'chat_id':'oc_testgroup','chat_type':'group'}, None, {}, lambda: types.SimpleNamespace(userid=None))
        self.assertIsNotNone(rejected)
        self.assertIsNone(rejected.userid)

    def test_ordinary_text_is_not_rewritten_as_command(self):
        result = self.parse('@_user_1 夜曲')
        self.assertEqual(result.text, '@_user_1 夜曲')

    def member_native(self, **kwargs):
        self.seen.append(kwargs)
        body = kwargs["body"]
        if str(body.get("text", "")).startswith("/"):
            return None  # Real v3.1.6 native parser rejects non-admin slash commands.
        return types.SimpleNamespace(userid="ou_bob", channel="飞书", source=kwargs["source"],
                                     text=body.get("text", ""), is_channel_admin=False)

    def member_parse(self, command, dispatch, *, enabled=True, **updates):
        body = dict(chat_id="oc_testgroup", chat_type="group", text=command, sender={"open_id":"ou_bob"})
        body.update(updates)
        return self.router.parse(self.member_native, "base", body, None, {},
                                 lambda: types.SimpleNamespace(userid=None),
                                 allow_music_members=enabled, on_music=dispatch)

    def test_group_member_music_is_dispatched_only_to_owned_handler_without_admin_flag(self):
        calls=[]
        result=self.member_parse("@_user_1 /music 七里香 周杰伦",lambda info,cmd,args:calls.append((info,cmd,args)))
        self.assertIsNone(result.userid)  # Stop global command/media processing.
        self.assertEqual(len(calls),1)
        info,cmd,args=calls[0]
        self.assertEqual((cmd,args),("/music","七里香 周杰伦"))
        self.assertEqual(info.userid,"ou_bob")
        self.assertFalse(info.is_channel_admin)
        message=self.message(info.source,userid="ou_bob")
        self.assertTrue(self.router.restore(message))
        self.assertEqual(message.original_chat_id,"oc_testgroup")

    def test_member_permission_does_not_dispatch_admin_commands_or_command_prefixes(self):
        for command in ["/restart","/update","/sites","/version","/musicx test","/music_statusx abc"]:
            with self.subTest(command=command):
                calls=[]
                self.member_parse(command,lambda *args:calls.append(args))
                self.assertEqual(calls,[])

    def test_member_permission_can_be_disabled_and_never_applies_to_private_chat(self):
        calls=[]
        result=self.member_parse("/music 夜曲",lambda *args:calls.append(args),enabled=False)
        self.assertIsNone(result.userid)
        self.assertEqual(calls,[])
        self.assertIsNone(self.member_parse("/music 夜曲",lambda *args:calls.append(args),chat_type="p2p"))
        self.assertEqual(calls,[])

    def test_member_chat_and_foreign_plugin_callbacks_are_not_forwarded_to_global_handlers(self):
        for body in [dict(text="普通群聊"),dict(text=None,type="cardAction",callback_data="[PLUGIN]OtherPlugin|restart")]:
            result=self.member_parse("",lambda *args:None,**body)
            self.assertIsNone(result.userid)

    def test_member_owned_music_callback_requires_member_permission(self):
        body=dict(type="cardAction",callback_data="[PLUGIN]MusicDownloadBridge|pick:s:1")
        allowed=self.member_parse("",lambda *args:None,**body)
        self.assertEqual(allowed.userid,"ou_bob")
        denied=self.member_parse("",lambda *args:None,enabled=False,**body)
        self.assertIsNone(denied.userid)

    def test_music_argument_cannot_execute_another_command(self):
        calls=[]
        self.member_parse("/music /restart",lambda info,cmd,args:calls.append((cmd,args)))
        self.assertEqual(calls,[("/music","/restart")])
