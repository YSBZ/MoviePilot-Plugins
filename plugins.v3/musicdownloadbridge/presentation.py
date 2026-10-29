"""Compact channel-neutral content using the host's native card/buttons."""
from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

PAGE_SIZE = 5
SOURCES = {'netease':'网易云', 'qq':'QQ音乐', 'kuwo':'酷我', 'kugou':'酷狗', 'migu':'咪咕', 'apple':'Apple Music'}
STATUS = {
    'submitting':'提交中', 'waiting':'等待中', 'queued':'排队中', 'accepted':'排队中',
    'pending':'等待中', 'running':'下载中', 'processing':'下载中', 'downloading':'下载中',
    'completed':'下载完成', 'failed':'下载失败', 'skipped':'已跳过', 'canceled':'已取消',
    'unknown':'状态待确认，稍后会自动重试查询',
}


def text(value: Any, limit: int = 80) -> str:
    value = ' '.join(str(value or '').split())
    if len(value) > limit:
        value = value[:limit-1] + '…'
    return re.sub(r'([\\`*_\[\]<>])', r'\\\1', value)


def search_page(query: str, session_id: str, songs: Sequence[Mapping[str, Any]], page: int = 0):
    pages = max(1, (len(songs)+PAGE_SIZE-1)//PAGE_SIZE)
    if page < 0 or page >= pages:
        raise ValueError('无效页码')
    blocks = [f'共 {len(songs)} 首 · 第 {page+1}/{pages} 页 · 15 分钟内有效']
    downloads = []
    for index, song in enumerate(songs[page*PAGE_SIZE:(page+1)*PAGE_SIZE], page*PAGE_SIZE+1):
        source = str(song.get('source') or '')
        details = [text(song.get('artist')) or '歌手未知', text(song.get('album'))]
        if source:
            details.append(text(SOURCES.get(source, source)))
        blocks.append(f'**{index:02d} · {text(song.get("name")) or "歌曲名称未知"}**\n'+' · '.join(v for v in details if v))
        downloads.append({'text':f'下载 {index:02d}', 'callback_data':f'[PLUGIN]MusicDownloadBridge|pick:{session_id}:{index}'})
    blocks.append(f'点击对应编号下载\n备用命令：/music_pick {session_id} 编号')
    buttons = [downloads[i:i+3] for i in range(0,len(downloads),3)]
    navigation = []
    if page:
        navigation.append({'text':'上一页', 'callback_data':f'[PLUGIN]MusicDownloadBridge|page:{session_id}:{page-1}'})
    if page+1 < pages:
        navigation.append({'text':'下一页', 'callback_data':f'[PLUGIN]MusicDownloadBridge|page:{session_id}:{page+1}'})
    if navigation:
        buttons.append(navigation)
    return f'音乐搜索 · {text(query,40)}', '\n\n'.join(blocks), buttons


def status_text(request: Mapping[str, Any], *, detailed: bool = False) -> str:
    song = request.get('song')
    song = song if isinstance(song, Mapping) else {}
    name = song.get('name')
    if not name and request.get('path'):
        name = PurePosixPath(str(request['path']).replace('\\','/')).name
    lines = []
    if name:
        lines.append('**'+text(name)+'**')
    detail = ' · '.join(text(song.get(k)) for k in ('artist','album') if song.get(k))
    if detail:
        lines.append(detail)
    status = str(request.get('status') or 'unknown')
    lines.append('状态：'+STATUS.get(status, text(status,40)))
    if status == 'completed':
        lines.append('下载完成，等待 MoviePilot 整理；尚未确认已入库。')
    if request.get('error'):
        lines.append('原因：'+text(request['error'],240))
    if request.get('warning'):
        lines.append('说明：'+text(request['warning'],240))
    if detailed:
        lines.append('任务：'+str(request.get('request_id') or ''))
        if request.get('path'):
            lines.append('文件：'+str(request['path']))
    return '\n'.join(lines)
