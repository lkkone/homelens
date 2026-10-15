# HomeLens

家庭网络和设备状态看板。基于项目所有者的 EmbyLens / ProxyLens 界面，Vue 3 + Python 标准库 + SQLite，无需 Home Assistant。

## 功能

- 后台填写管理地址、品牌、账号和密码；当前适配中兴 SR1001（vue_topo 固件），其他品牌可通过 `server/adapters` 注册。
- 从路由器自动发现设备，合并 LAN 记录与 Mesh 客户端数据，显示在线状态、IP、MAC、频段、信号、接入节点和实时上下行速率。
- 名称、房间分组、备注、展示方式、离线提醒均只保存在 HomeLens，不更改路由器配置。
- 默认仅展示在线设备；可设置始终展示或隐藏，正常休眠设备不自动触发告警。
- 采集每 30 秒一次，网页每 5 秒读取本地缓存。历史最多保留最近五小时的 60 个真实采样点，五分钟记录一次，状态变化时额外记录；不补造历史。
- 路由器不可访问时标为未知，不把全部设备误判为离线。认证失败停止重复登录，保存配置后恢复；不会自动接管其他管理员的会话。
- 当前速率是实时流量，不是宽带测速结果。CPU、内存、磁盘等主机指标不在首版范围内。

## 部署

复制 `.env.example` 为 `.env`，填写管理账号密码，构建镜像后运行：

```sh
docker build -t homelens:0.1.0-arm64 .
mkdir -p data
# 镜像使用 UID/GID 10001；目录需允许该用户写入。
docker compose up -d
```

默认监听 18793，后台路径 `/admin`。SQLite、设备自定义信息和历史保存在绑定的 `data` 目录。管理密码仅在后端使用，API 不返回明文；部署文件和数据库应仅允许管理员读取。

采集程序需要能直接访问路由器局域网。程序明确禁用环境代理访问路由器。容器默认使用 bridge 网络，Mac Docker Desktop 可通过宿主机访问家中 LAN。

## 开发与测试

```sh
python -m unittest discover -s tests
npm --prefix frontend ci
npm --prefix frontend run build
```

把 `frontend/dist` 复制到 `server/web` 后，设置 `ADMIN_USERNAME`、`ADMIN_PASSWORD`，运行 `python server/app.py`。通过 `COLLECTOR_ENABLED=false` 可运行不访问真实路由器的界面测试。
