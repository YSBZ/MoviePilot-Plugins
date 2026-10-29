"""Carry a signed group reply route through the host's public source field.

MP 3.1.6 command events preserve source/user but omit original_chat_id.
Do not use a mutable 'last chat for this user' map: private and group requests
may overlap. User identities and native permission checks remain untouched.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any, Callable


class FeishuGroupRouter:
    PREFIX = 'mgrp1.'

    def __init__(self, group_id: str, key: bytes):
        if not re.fullmatch(r'oc_[A-Za-z0-9_]{1,160}', group_id) or len(key) < 32:
            raise ValueError('invalid Feishu group route configuration')
        self.group_id = group_id
        self.key = key

    def _signature(self, raw: bytes) -> str:
        return hmac.new(self.key, self.group_id.encode() + b'\0' + raw, hashlib.sha256).hexdigest()[:32]

    def _encode(self, source: str) -> str:
        raw = source.encode()
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip('=')
        return self.PREFIX + encoded + '.' + self._signature(raw)

    def _decode(self, source: str) -> str | None:
        if not source.startswith(self.PREFIX) or len(source) > 2048:
            return None
        try:
            encoded, signature = source[len(self.PREFIX):].split('.')
            raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            if not hmac.compare_digest(signature, self._signature(raw)):
                return None
            return raw.decode() or None
        except (ValueError, UnicodeError):
            return None

    def parse(self, native: Callable[..., Any], source: str, body: Any, form: Any,
              args: Any, rejected: Callable[[], Any], *, allow_music_members: bool = False,
              on_music: Callable[[Any, str, str], Any] | None = None) -> Any:
        if isinstance(body, (str, bytes, bytearray)):
            try:
                body = json.loads(body)
            except (ValueError, UnicodeError):
                return None
        if not isinstance(body, Mapping) or body.get('chat_id') != self.group_id:
            return None
        if body.get('chat_type') != 'group' and body.get('type') != 'cardAction':
            return None
        if not source or source.startswith(self.PREFIX):
            return None
        normalized = dict(body)
        text = normalized.get('text')
        if isinstance(text, str):
            normalized['text'] = re.sub(r'^(?:\s*@_user_\d+\s*)+(?=/)', '', text).strip()
        normalized_text = str(normalized.get('text') or '')
        music = re.fullmatch(r'/(music|music_pick|music_status)(?:\s+(.*))?', normalized_text, re.DOTALL)
        member_music = bool(allow_music_members and on_music and music)
        if member_music:
            # Native MP treats every slash command as administrative. Parse this
            # strictly allowlisted input as ordinary text to obtain the REAL user
            # and role facts, then dispatch ONLY our owned music handler below.
            # It never reaches the host's global command registry.
            normalized['text'] = normalized_text[1:]
        incoming = native(source=source, body=normalized, form=form, args=args)
        if incoming is None:
            # A truthy empty IncomingMessage prevents a second native fallback
            # from reinterpreting the unnormalized text after permission rejection.
            return rejected()
        incoming.source = self._encode(source)
        if member_music:
            if getattr(incoming, 'userid', None):
                incoming.text = normalized_text
                on_music(incoming, '/' + music.group(1), (music.group(2) or '').strip())
            return rejected()
        if getattr(incoming, 'is_channel_admin', False) is not True:
            own_callback = body.get('type') == 'cardAction' and str(body.get('callback_data') or '').startswith('[PLUGIN]MusicDownloadBridge|')
            if not allow_music_members or not own_callback:
                return rejected()
        return incoming

    def restore(self, message: Any) -> bool:
        channel = getattr(message, 'channel', None)
        if str(getattr(channel, 'value', channel)).lower() not in ('feishu', '飞书'):
            return False
        source = self._decode(str(getattr(message, 'source', '') or ''))
        if not source:
            return False
        message.source = source
        if not getattr(message, 'private_delivery', False):
            message.original_chat_id = self.group_id
        return True
