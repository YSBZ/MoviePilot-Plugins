# mpbot 命令说明

依据 NAS MoviePilot 当前注册命令核对，更新日期：2026-10-29。当前共 20 条。

私聊直接发送；群聊请先 @mpbot，再输入命令。音乐候选有按钮时可直接点击，纯文本命令用于回退。当前沿用机器人管理员白名单，管理命令不向其他成员开放。

| 分类 | 命令与用法 | 功能 |
| --- | --- | --- |
| 其他 | `/redo` | 手动整理 |
| 智能体 | `/session_status` | 会话状态 |
| 智能体 | `/skills` | 管理技能 |
| 站点 | `/cookiecloud` | 同步站点 |
| 站点 | `/site_signin` | 站点签到 |
| 站点 | `/sites` | 管理站点 |
| 管理 | `/clear_cache` | 清理缓存 |
| 管理 | `/clear_session` | 清除会话 |
| 管理 | `/downloading` | 正在下载 |
| 管理 | `/mediaserver_sync` | 同步媒体服务器 |
| 管理 | `/restart` | 重启系统 |
| 管理 | `/stop_agent` | 停止推理 |
| 管理 | `/transfer` | 下载文件整理 |
| 管理 | `/update` | 检查更新 |
| 管理 | `/version` | 当前版本 |
| 订阅 | `/douban_sync` | 同步豆瓣想看 |
| 订阅 | `/subscribes` | 管理订阅 |
| 音乐 | `/music 歌名或歌手` | 搜索歌曲 |
| 音乐 | `/music_pick 会话ID 编号` | 选择歌曲并下载 |
| 音乐 | `/music_status 请求ID` | 查询音乐下载状态 |

## 音乐使用流程

1. 发送 `/music 夜曲` 搜索。
2. 点击返回的候选按钮，或按回复中的会话 ID 执行 `/music_pick 会话ID 1`。
3. 使用 `/music_status 请求ID` 查看该次下载状态。下载完成后由 MP 目录监控整理，再由 Navidrome 扫描；下载成功不代表已经整理入库。

`/restart`、`/update`、`/clear_cache`、`/transfer` 等会触发实际操作，请按需使用。命令可见不代表对应站点、订阅、智能体或插件已经配置可用。
