# MP v3.1.6 兼容检查

目标：官方 `jxxghp/MoviePilot` 的 `v3.1.6` 标签。检查日期：2026-10-29。

插件 SDK 导出、抽象基类方法、消息发送的 source/buttons 参数、命令的 channel/source/user/arg_str 字段、MessageAction 回调字段、interval 定时服务格式及 requests 依赖均与插件使用方式匹配。无需业务代码迁移；原先 3.2.1 是未经旧版本核对的保守声明。

契约测试从外部提供的官方源码通过 AST 提取并执行未改动的 `_PluginBase`，保留抽象方法检查及 get_config/get_data/save_data/post_message 行为，仅替换数据库与通知传输等基础设施；复用已有搜索、选择、状态与重启恢复测试。这不等于完整 MP 服务启动或 NAS 验收。

运行方式（源码目录须为官方 v3.1.6）：

```sh
MP_HOST_CONTRACT_ROOT=/path/to/MoviePilot .venv/bin/python -m unittest discover -s tests/v3/musicdownloadbridge -v
```

未设置该变量时，宿主契约用例会明确跳过。上游代码不随插件分发。

## 核对来源

- [app/sdk/plugin/__init__.py](https://github.com/jxxghp/MoviePilot/blob/v3.1.6/app/sdk/plugin/__init__.py)；SHA-256 `05299d1e2327345ad7fccb976b6411c2877717d4dfd98d4de99320c01564ba3c`
- [app/sdk/plugin/base.py](https://github.com/jxxghp/MoviePilot/blob/v3.1.6/app/sdk/plugin/base.py)；SHA-256 `b25998aa63c7553ff269dbb541574e48d774c93f0f08e1e96ac079c7b5ba9b6a`
- [app/sdk/events.py](https://github.com/jxxghp/MoviePilot/blob/v3.1.6/app/sdk/events.py)；SHA-256 `ad331361bdcbdc3bec743cffdaf3efa3dfbc7245307342b18402a6f83bf7d5f1`
- [app/command.py](https://github.com/jxxghp/MoviePilot/blob/v3.1.6/app/command.py)；SHA-256 `38526e41cff4d85fcece7af8f3522d060a9be7fdeaa317b93439dcbb39b9959a`
- [app/chain/message.py](https://github.com/jxxghp/MoviePilot/blob/v3.1.6/app/chain/message.py)；SHA-256 `af8fe2c615cc55c6e983c68b0b4f052ad2d42b9e83a109f1658a94b7aa03b0bb`
- [pyproject.toml](https://github.com/jxxghp/MoviePilot/blob/v3.1.6/pyproject.toml)；SHA-256 `65bf1fd2b59feefc4bc41a9a160f72afc0a24f11cb4001e1358be43f533bb45d`

## 尚待验证

NAS 插件安装和真实加载、飞书音乐命令、定制下载服务真实音源下载、MP 整理及 Navidrome 入库。不能将本机契约测试视为这些链路已经通过。
