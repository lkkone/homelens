# HomeLens

家庭网络和设备状态看板。基于项目所有者的 EmbyLens / ProxyLens 界面，Vue 3 + Python 标准库 + SQLite，无需 Home Assistant。

## 功能

- 后台填写管理地址、品牌、账号和密码；当前适配中兴 SR1001（vue_topo 固件），其他品牌可通过 `server/adapters` 注册。
- 从路由器自动发现设备，合并 LAN 记录与 Mesh 客户端数据，显示在线状态、IP、MAC、频段、信号、接入节点和实时上下行速率。
- 名称、房间分组、备注、展示方式、离线提醒均只保存在 HomeLens，不更改路由器配置。
- 新发现设备默认隐藏；在后台通过「是否展示」开关逐台开启。离线提醒单独设置，正常休眠设备不自动触发告警。
- 采集每 30 秒一次，网页每 5 秒读取本地缓存。设备展示真实上下行速率曲线，每分钟记录一次、保留两小时；在线为绿色曲线，离线为灰色基线，采集失败留空，不补造历史，也不逐台发送探活请求。
- 路由器不可访问时标为未知，不把全部设备误判为离线。认证失败停止重复登录，保存配置后恢复；不会自动接管其他管理员的会话。
- 当前速率是路由器返回的实时流量。可选的宽带测速在指定时区每天 00:00 独立运行一次，显示采集节点互联网出口的下载、上传与延迟，失败不影响在线状态。CPU、内存、磁盘等主机指标不在首版范围内。

## 部署

复制 `.env.example` 为 `.env`，填写管理账号密码，构建镜像后运行：

```sh
docker build -t homelens:0.1.0-arm64 .
mkdir -p data
# 镜像使用 UID/GID 10001；目录需允许该用户写入。
docker compose up -d
```

默认监听 18793，后台路径 `/admin`。SQLite、设备自定义信息和历史保存在绑定的 `data` 目录。管理密码仅在后端使用，API 不返回明文；部署文件和数据库应仅允许管理员读取。

从 0.1.0 升级时，旧版「自动」展示且当时在线（或启用了离线提醒）的设备会保留展示；其余设备改为隐藏，可在后台逐台开启。旧版 `samples` 表保留供回退；新流量曲线从升级后开始积累，不伪造旧数据。升级前建议备份 SQLite 数据库。

采集程序需要能直接访问路由器局域网。程序明确禁用环境代理访问路由器。容器默认使用 bridge 网络，Mac Docker Desktop 可通过宿主机访问家中 LAN。

## 开发与测试

```sh
python -m unittest discover -s tests
npm --prefix frontend ci
npm --prefix frontend run build
```

本地运行时设置 `WEB_DIR=frontend/dist`，并设置 `ADMIN_USERNAME`、`ADMIN_PASSWORD`，运行 `python server/app.py`。通过 `COLLECTOR_ENABLED=false` 可运行不访问真实路由器的界面测试。默认端口 18793；若正在运行正式容器，可用 `PORT=18794` 启动开发实例。

## 每日宽带测速（可选）

默认关闭。构建时添加 `--build-arg INSTALL_SPEEDTEST=true` 安装校验摘要固定的 Ookla Speedtest CLI（amd64 / arm64），并设置 `SPEEDTEST_ENABLED=true`、`SPEEDTEST_TIMEZONE=Asia/Shanghai`。使用 Ookla CLI 需符合其许可条件；[官方说明](https://www.speedtest.net/apps/cli)。本项目不分发 CLI 二进制源码。

在所选时区的 00:00–00:05 启动窗口内运行一次，数据库按日期防重复，最长三分钟；重启不会重复测速，也不会在白天补跑。首次结果等待下一次午夜；失败保留上次成功结果及其时间。测速短时占用宽带，结果是采集节点到公网测速服务器的速度，并非各设备的 Wi-Fi 速度。只保存速率、延迟、节点名称，不保存公网 IP。

本地直接运行 Python 时需系统提供时区数据库，并安装 `speedtest` 命令或通过 `SPEEDTEST_BIN` 指定路径。Docker 镜像已包含时区数据库。
