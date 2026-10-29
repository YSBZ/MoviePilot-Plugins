# MusicDownloadBridge

## 在线安装与当前状态

插件仓库地址：`https://github.com/YSBZ/MoviePilot-Plugins`。将该地址加入 MoviePilot 的插件仓库配置，然后在插件市场搜索“音乐下载桥接”。已有插件源请保留。

当前为候选版本：要求 MP >= 3.2.1；MP v3.1.6 兼容性尚未验证，不能直接安装。14 项本机测试通过，但尚未验证真实宿主加载和 NAS 下载闭环。需要配套提供 `/api/music-bridge/` API 的定制 go-music-dl；官方原版镜像不包含该 API。

在线源安装完成后不再需要本地仓库路径；切换前保留现有配置以便回退。

MoviePilot V3.2.1+ 插件。它调用独立音乐服务搜索候选、提交单曲下载并查询状态；下载文件落入现有共享下载目录后，由 MoviePilot 的目录监控继续整理。本插件只报告下载服务给出的完成状态，不表示歌曲已经整理入库。当前按父级部署选择飞书为首选渠道，仍保留命令回退。

HTTP 客户端复用 MoviePilot 宿主提供的 `requests~=2.34.2` 版本约束，插件不会单独创建运行环境或要求升级共享依赖。

## MoviePilot 配置

在插件设置页填写：

- **音乐下载服务地址**：包含服务路由前缀，例如 `http://NAS-IP:8085/music`。插件会在其后请求 `/api/music-bridge/...`。
- **Bearer token**：与服务端配置一致；插件不会把 token 写入日志或消息。
- **音源**：可用逗号分隔的来源 ID，例如 `netease,qq`；留空时服务端使用默认来源。
- **轮询间隔**：5 到 60 秒，默认 20 秒。

启用前，需先在同一网络中部署提供本仓库约定 API 的音乐服务，并确认 MoviePilot 容器可访问其地址。完成配置后启用插件。

## 消息命令

- `/music 夜曲`：搜索歌曲并显示候选。若渠道支持 `[PLUGIN]` 回调，会同时显示快捷按钮。
- `/music_pick <会话ID> <编号>`：选择候选并提交下载。按钮不可用时使用此显式命令。
- `/music_status <请求ID>`：查看自己的下载状态。

搜索会话 15 分钟后过期。会话和请求按发起用户、消息来源及渠道隔离。对同一候选的重复选择复用同一请求 ID；失败后再次执行对应 `/music_pick` 会创建一次新的重试请求。状态轮询由 MoviePilot 插件服务调度，不会在消息处理器中等待实际下载。

## 本机测试

本仓库测试使用本机临时 HTTP 服务和最小假宿主，不需要机器人 token 或 NAS：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements.txt
.venv/bin/python -m unittest discover -s tests/v3/musicdownloadbridge -v
```

它验证 HTTP 路由和 Bearer 认证、下载提交与重启恢复、重复选择、权限隔离、过期会话、失败重试、消息路由和状态呈现。真实 MoviePilot V3 宿主加载、飞书真实消息收发、真实音源和 NAS 部署仍需在对应环境验收。
