from __future__ import annotations
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'plugins.v3/musicdownloadbridge'))
from presentation import search_page, status_text


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.songs = [dict(id=str(i), name=f'歌曲 {i}', artist='歌手', album='专辑', source='netease') for i in range(1,11)]

    def test_first_page_compact_buttons_and_complete_manual_fallback(self):
        title,text,buttons=search_page('搜索词','session123',self.songs,0)
        self.assertIn('搜索词',title)
        self.assertIn('歌曲 5',text)
        self.assertNotIn('歌曲 6',text)
        self.assertIn('1/2',text)
        self.assertIn('/music_pick session123 编号',text)
        self.assertTrue(all(len(row)<=3 for row in buttons))
        downloads=[b for row in buttons for b in row if '|pick:' in b['callback_data']]
        self.assertEqual(len(downloads),5)
        self.assertEqual(downloads[0]['text'],'下载 01')
        self.assertTrue(any('下一页' in b['text'] for row in buttons for b in row))

    def test_second_page_preserves_global_indices_and_includes_tenth_candidate(self):
        _,text,buttons=search_page('搜索词','session123',self.songs,1)
        self.assertIn('歌曲 10',text)
        values=[b['callback_data'] for row in buttons for b in row]
        self.assertIn('[PLUGIN]MusicDownloadBridge|pick:session123:10',values)
        self.assertTrue(any('|page:session123:0' in v for v in values))
        with self.assertRaises(ValueError):search_page('搜索词','s',self.songs,2)

    def test_remote_metadata_cannot_inject_extra_lines_or_markdown_actions(self):
        self.songs[0]['name']='歌曲\n<script> [点我](https://example.invalid) *'
        _,text,_=search_page('词','s',self.songs,0)
        self.assertNotIn('\n<script>',text)
        self.assertNotIn('[点我]',text)

    def test_automatic_status_is_about_song_and_detailed_status_preserves_debug_data(self):
        request=dict(request_id='a'*32,status='completed',path='/app/downloads/song.mp3',song=dict(name='夜曲',artist='周杰伦',album='十一月的萧邦'))
        compact=status_text(request)
        self.assertIn('夜曲',compact)
        self.assertIn('周杰伦',compact)
        self.assertNotIn('/app/downloads',compact)
        self.assertNotIn('a'*32,compact)
        detailed=status_text(request,detailed=True)
        self.assertIn('/app/downloads/song.mp3',detailed)
        self.assertIn('a'*32,detailed)
        self.assertIn('尚未确认已入库',compact)
