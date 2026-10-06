# 部署指南

本项目支持 **6 种部署方式**，按你的环境挑一种即可。

| 方式 | 适合谁 | 需要 Docker | 难度 |
|---|---|---|---|
| [一、Docker Compose](#方式一docker-compose推荐) | 所有人，**默认推荐** | ✅ | ★ |
| [二、docker run 单命令](#方式二docker-run-单命令免-compose) | 不想用 compose 文件 | ✅ | ★ |
| [三、构建自包含镜像](#方式三构建自包含镜像) | 要多机分发 / 推自己的镜像仓库 | ✅ | ★★ |
| [四、NAS 图形面板](#方式四nas-图形面板点点点) | 群晖 / 飞牛 / 威联通 / Unraid / TrueNAS | ✅ | ★ |
| [五、裸机 Python + systemd](#方式五裸机运行不用-docker) | NAS 没装 Docker、软路由、老设备 | ❌ | ★★ |
| [六、第三方 Docker 面板](#方式六第三方-docker-面板) | 已有 Portainer / Dockge / 1Panel | ✅ | ★ |

> 下面所有方式都在这台机器上**真机跑通过**（飞牛 fnOS / x86_64 / Docker 27），不是纸面推演。

---

## 零、通用准备（**所有方式都要先做这一步**）

### 0.1 先把代码放到机器上 —— 这一步不能省

这是最容易踩的坑：**本项目的容器把宿主机上的 `server.py` 和 `src/` 挂载进容器**，它们不在镜像里。所以不管用哪种方式，都**必须先让这两个文件存在于宿主机上**，否则容器起来会直接报 `bind mount failed` 或者服务 502。

三种做法，任选其一：

**做法 A：git clone（推荐，能一条命令升级）**

```bash
# 换成你想放的目录
cd /opt
git clone https://github.com/bestmingyang/gift-book-nas.git
cd gift-book-nas
```

**做法 B：下载 ZIP 解压**

```bash
cd /opt
curl -L -o gbn.zip https://github.com/bestmingyang/gift-book-nas/archive/refs/heads/main.zip
unzip gbn.zip && mv gift-book-nas-main gift-book-nas && cd gift-book-nas
```

**做法 C：只下载两个必需文件**（不用 git、也不用解压整包）

```bash
mkdir -p /opt/gift-book-nas/src
cd /opt/gift-book-nas
curl -LO https://raw.githubusercontent.com/bestmingyang/gift-book-nas/main/server.py
curl -LO https://raw.githubusercontent.com/bestmingyang/gift-book-nas/main/docker-compose.yml
# src 目录下的前端文件需逐个下载，建议还是用做法 A/B
```

### 0.2 确认端口没被占用

默认用宿主机 **8094** 端口。先看一下：

```bash
# Linux / NAS
ss -tlnp | grep 8094 || echo "8094 空闲"

# macOS
lsof -i :8094 || echo "8094 空闲"
```

被占用了就换一个，比如 8095，改 `docker-compose.yml` 里 `ports:` 的左边那个数字。

> ⚠️ 冒号**右边**的 `3000` 是容器内部端口，**不要动**。改左边即可：`"8095:3000"`。

### 0.3 想好数据放哪

默认数据落在项目旁边的 `./data`。如果你有独立大盘（NAS 上一般都有），建议指过去，例如：

```yaml
volumes:
  - "/vol00/YOUR-DISK/gift-book-data:/data"
```

**这个目录是你全部礼簿数据的所在**，备份、迁移都只需要搬它。

---

## 方式一：Docker Compose（推荐）

最省事，也是本项目默认方式。

```bash
git clone https://github.com/bestmingyang/gift-book-nas.git
cd gift-book-nas
docker compose up -d
```

打开 `http://<宿主机IP>:8094` 即可。

**验证是否正常**

```bash
docker compose ps                              # 状态应为 running
curl http://127.0.0.1:8094/api/health          # 应返回 {"ok": true, ...}
```

**日常操作**

```bash
docker compose logs -f          # 看日志
docker compose restart          # 重启
docker compose down             # 停止（数据保留）
docker compose up -d            # 升级后重启
```

**升级**

```bash
git pull                        # 拉最新代码
docker compose up -d            # 重建容器（数据不受影响）
```

> 本方式下代码是**挂载**进容器的，所以改 `src/index.html` 后刷新浏览器即可生效，不用重建镜像——方便自己魔改界面。

---

## 方式二：docker run 单命令（免 compose）

不想用 compose 文件，或者你的环境没有 compose 插件：

```bash
cd /opt/gift-book-nas       # 即前面放好代码的目录

docker run -d \
  --name gift-book \
  -p 8094:3000 \
  -w /app \
  -e GB_DATA_DIR=/data \
  -e GB_STATIC_DIR=/home/static \
  -e GB_PORT=3000 \
  -e TZ=Asia/Shanghai \
  -v /opt/gift-book-nas/server.py:/app/server.py:ro \
  -v /opt/gift-book-nas/src:/home/static:ro \
  -v /opt/gift-book-nas/data:/data \
  --restart unless-stopped \
  python:3-alpine python3 /app/server.py
```

**每一行在做什么**

| 参数 | 作用 |
|---|---|
| `-p 8094:3000` | 宿主机 8094 → 容器 3000 |
| `-w /app` | 工作目录，必须与挂载 `server.py` 的位置一致 |
| `-e GB_DATA_DIR=/data` | 数据落盘位置（容器内路径） |
| `-e GB_STATIC_DIR=/home/static` | 网页目录（容器内路径） |
| `-v .../server.py:/app/server.py:ro` | 把后端挂进去（`:ro` 只读） |
| `-v .../src:/home/static:ro` | 把前端挂进去 |
| `-v .../data:/data` | **数据目录，必须挂**，否则数据写进容器层，删容器就没了 |
| `--restart unless-stopped` | 开机 / 崩溃后自动拉起 |

> 把上面几条 `-v` 的左边换成你自己的真实绝对路径。

**停机 / 删除**

```bash
docker stop gift-book && docker rm gift-book     # 数据在宿主机目录里，不会丢
```

---

## 方式三：构建自包含镜像

前面两种方式都是「挂载源码」。如果你想把整个应用**打成一个镜像**，方便推到自己的私有仓库、分发到多台机器、或者给不熟悉挂载的人用，用这个方式。

仓库里已经带了 `Dockerfile`：

```bash
cd /opt/gift-book-nas

# 1) 构建
docker build -t gift-book-nas:latest .

# 2) 运行（只挂数据目录，不挂源码）
docker run -d \
  --name gift-book \
  -p 8094:3000 \
  -v /opt/gift-book-nas/data:/data \
  --restart unless-stopped \
  gift-book-nas:latest
```

镜像约 **67 MB**（基于 `python:3-alpine`），已内置 `HEALTHCHECK`，`docker ps` 里能看到 `(healthy)`。

**推到自己的镜像仓库**

```bash
docker tag gift-book-nas:latest registry.example.com/你的用户名/gift-book-nas:latest
docker push registry.example.com/你的用户名/gift-book-nas:latest
```

**对应的 compose 写法**（想用 compose 管这个镜像的话）：

```yaml
services:
  gift-book:
    image: gift-book-nas:latest     # 或你的私有仓库地址
    container_name: gift-book
    ports:
      - "8094:3000"
    volumes:
      - ./data:/data                # 只挂数据，源码已在镜像里
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
```

> 代价：这样改前端要**重新 build** 才生效，不像方式一那样改完刷新就行。自己长期魔改的话，建议还是用方式一。

---

## 方式四：NAS 图形面板（点点点）

各家 NAS 的界面叫法不同，但套路一样：**新建项目 → 粘贴 YAML → 部署**。

> ⚠️ **再次强调**：粘贴 YAML 之前，必须先把 `server.py` 和 `src/` 放到 NAS 上（见 [0.1](#01-先把代码放到机器上--这一步不能省)）。面板不会替你下载源码。
>
> 并且**面板场景建议把挂载路径改成绝对路径**，因为面板给项目分配的目录和 `./` 的相对基准不一定一致。

### 4.1 飞牛 fnOS

1. 用「文件管理」把代码放到 `/vol1/docker/gift-book-nas`（git clone 或直接上传）
2. 打开 **Docker** → 左侧「**容器**」→ 右上角「**添加**」
3. 弹窗里选「**docker-compose**」标签页
4. 粘贴下面的配置 → 确定

```yaml
services:
  gift-book:
    image: python:3-alpine
    container_name: gift-book
    working_dir: /app
    command: ["python3", "/app/server.py"]
    ports:
      - "8094:3000"
    environment:
      - GB_DATA_DIR=/data
      - GB_STATIC_DIR=/home/static
      - GB_PORT=3000
      - TZ=Asia/Shanghai
    volumes:
      - /vol1/docker/gift-book-nas/server.py:/app/server.py:ro
      - /vol1/docker/gift-book-nas/src:/home/static:ro
      - /vol1/docker/gift-book-nas/data:/data
    restart: unless-stopped
```

5. 部署完成后访问 `http://飞牛IP:8094`

> 飞牛上习惯把容器配置都放在 `/vol1/docker/` 下，按这个惯例走即可。想让数据落到机械盘，把最后一行换成 `/vol00/你的盘/gift-book-data:/data`。

### 4.2 群晖 DSM 7.2+

群晖的 Docker 叫「**Container Manager**」（DSM 7.2 之前在套件中心叫 Docker）。

1. 先在「**File Station**」里建好目录：`/volume1/docker/gift-book-nas`，把代码放进去
2. 套件中心安装 **Container Manager** → 打开 → 左侧「**项目**」→ 右上角「**新增**」
3. 填写：
   - **项目名称**：`gift-book`（英文，别带特殊字符）
   - **路径**：`/volume1/docker/gift-book-nas`
   - **来源**：选「**创建 docker-compose.yml**」
4. 下一步 → 在编辑器里粘贴 YAML → 点「**验证**」
5. 勾选「**创建项目后立即启动**」→ 完成

```yaml
services:
  gift-book:
    image: python:3-alpine
    container_name: gift-book
    working_dir: /app
    command: ["python3", "/app/server.py"]
    ports:
      - "8094:3000"
    environment:
      - GB_DATA_DIR=/data
      - GB_STATIC_DIR=/home/static
      - GB_PORT=3000
      - TZ=Asia/Shanghai
    volumes:
      - /volume1/docker/gift-book-nas/server.py:/app/server.py:ro
      - /volume1/docker/gift-book-nas/src:/home/static:ro
      - /volume1/docker/gift-book-nas/data:/data
    restart: unless-stopped
```

**⚠️ 群晖专属坑（几乎人人都会撞）**

- 群晖的 Docker **不会自动创建** YAML 里声明的挂载目录，而 Linux 上会自动创建。所以**先手动建好** `server.py` 所在目录和 `data` 目录，否则报错：
  `Error response from daemon: Bind mount failed: ... does not exist`
  解决：File Station 里补建目录 → 回到「项目」→ 选中项目 → 「动作」→「**构建**」重新拉起。
- **端口别撞车**：群晖 DSM 自己占 5000 / 5001，别把 8094 改成这俩。
- 存储路径统一是 `/volume1/...`（多卷就是 `/volume2/...`），不要写 `/opt` 之类的 Linux 常规路径。

### 4.3 威联通 QNAP

需要 **Container Station 3.0+**（QTS 5.1 以上）。

1. App Center 安装 **Container Station** → 打开
2. 左侧「**应用程序**」（Applications）→「**创建**」
3. 输入应用名 `gift-book` → 在编辑器里粘贴 YAML → 点「**验证**」→「**创建**」

```yaml
services:
  gift-book:
    image: python:3-alpine
    container_name: gift-book
    working_dir: /app
    command: ["python3", "/app/server.py"]
    ports:
      - "8094:3000"
    environment:
      - GB_DATA_DIR=/data
      - GB_STATIC_DIR=/home/static
      - GB_PORT=3000
      - TZ=Asia/Shanghai
    volumes:
      - /share/Container/gift-book-nas/server.py:/app/server.py:ro
      - /share/Container/gift-book-nas/src:/home/static:ro
      - /share/Container/gift-book-nas/data:/data
    restart: unless-stopped
```

> QNAP 的共享目录真实路径通常是 `/share/CACHEDEV1_DATA/...`，而 `/share/Container/...` 是它的软链别名，用哪个都行。以你在 File Station 里看到的实际路径为准。
>
> 部分型号（尤其 ARM 机型）的 Container Station 对 Compose 支持不完整，若界面找不到「应用程序」入口，改用方式二的 `docker run`（SSH 执行）。

### 4.4 Unraid

Unraid 7.x 增强了 Compose 支持；老版本需要装插件。

1. **Apps**（Community Applications）里搜「**Compose Manager**」→ 安装（`Compose Manager Plus` 是目前维护中的版本）
2. 打开 **Docker** → 「**Compose**」标签 → 「**Add Stack**」
3. 起名 `gift-book` → 保存 → 点齿轮进「**Edit Stack**」→ 「Compose File」里粘贴 YAML → 保存
4. 点「**Compose Up**」启动

```yaml
services:
  gift-book:
    image: python:3-alpine
    container_name: gift-book
    working_dir: /app
    command: ["python3", "/app/server.py"]
    ports:
      - "8094:3000"
    environment:
      - GB_DATA_DIR=/data
      - GB_STATIC_DIR=/home/static
      - GB_PORT=3000
      - TZ=Asia/Shanghai
    volumes:
      - /mnt/user/appdata/gift-book-nas/server.py:/app/server.py:ro
      - /mnt/user/appdata/gift-book-nas/src:/home/static:ro
      - /mnt/user/appdata/gift-book-nas/data:/data
    restart: unless-stopped
```

**⚠️ Unraid 专属坑**

- Compose Manager 默认项目目录在 `/boot/config/plugins/compose.manager/projects/`（**U 盘**上），权限受限，挂载容易失败。所以**代码和数据一律放 `/mnt/user/appdata/`**（缓存池），别放 `/boot`。
- 挂载目录要**先在终端建好**：`mkdir -p /mnt/user/appdata/gift-book-nas/data`。

### 4.5 TrueNAS SCALE 24.10+

TrueNAS 从 24.10（Electric Eel）起用 Docker 跑应用，支持直接贴 Compose。

1. **Datasets** 里建数据集，例如 `apps/gift-book-nas`（实际路径 `/mnt/你的池名/apps/gift-book-nas`）
2. 用 Shell 或 SMB 把代码放进去
3. **Apps** → **Discover Apps** → 右上角「**⋮**」→「**Install via YAML**」
4. 填应用名 `gift-book` → 粘贴 YAML → **Save**

```yaml
name: gift-book
services:
  gift-book:
    image: python:3-alpine
    container_name: gift-book
    working_dir: /app
    command: ["python3", "/app/server.py"]
    ports:
      - "8094:3000"
    environment:
      - GB_DATA_DIR=/data
      - GB_STATIC_DIR=/home/static
      - GB_PORT=3000
      - TZ=Asia/Shanghai
    volumes:
      - /mnt/你的池名/apps/gift-book-nas/server.py:/app/server.py:ro
      - /mnt/你的池名/apps/gift-book-nas/src:/home/static:ro
      - /mnt/你的池名/apps/gift-book-nas/data:/data
    restart: unless-stopped
```

> 注意 TrueNAS 的 Custom App 要求 YAML 顶层有 `name:` 字段（见上面第一行），这是它和标准 compose 的唯一差别。
>
> 也正因为如此，TrueNAS 场景其实更推荐**方式三（构建镜像）**：把源码打进镜像，就不用纠结数据集路径挂载了。

---

## 方式五：裸机运行（不用 Docker）

**完全不需要 Docker。** 本项目后端只用 Python 标准库，没有任何第三方依赖，**Python 3.8+ 直接跑**。

适合：NAS 没装 Docker、软路由、老旧设备、Windows/macOS 本机临时用。

### 5.1 先试跑一下

```bash
cd /opt/gift-book-nas

GB_DATA_DIR=/opt/gift-book-nas/data \
GB_STATIC_DIR=/opt/gift-book-nas/src \
GB_PORT=8094 \
python3 server.py
```

看到 `[giftbook] 服务已启动` 就成功了，浏览器打开 `http://<IP>:8094`。

> Windows 上把环境变量换成 `set GB_PORT=8094`（cmd）或 `$env:GB_PORT="8094"`（PowerShell）。
> macOS 自带的 `python3` 就能跑，无需额外安装。

**临时后台跑（重启后失效，只适合试）**

```bash
nohup env GB_DATA_DIR=./data GB_STATIC_DIR=./src GB_PORT=8094 python3 server.py > giftbook.log 2>&1 &
```

### 5.2 做成 systemd 服务（推荐，开机自启 + 崩溃自愈）

Linux 上「正经」的常驻方式是交给 systemd 管。

新建 `/etc/systemd/system/giftbook.service`：

```ini
[Unit]
Description=Gift Book NAS
After=network.target

[Service]
Type=simple
# 换成你想跑这个服务的用户（建议非 root，例如你自己）
User=youruser
WorkingDirectory=/opt/gift-book-nas
Environment=GB_DATA_DIR=/opt/gift-book-nas/data
Environment=GB_STATIC_DIR=/opt/gift-book-nas/src
Environment=GB_PORT=8094
Environment=TZ=Asia/Shanghai
ExecStart=/usr/bin/python3 /opt/gift-book-nas/server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo mkdir -p /opt/gift-book-nas/data
sudo chown -R youruser /opt/gift-book-nas     # 数据目录要能写

sudo systemctl daemon-reload
sudo systemctl enable --now giftbook
sudo systemctl status giftbook                 # 应为 active (running)
```

日常管理：

```bash
sudo systemctl restart giftbook     # 重启
sudo systemctl stop giftbook        # 停止
journalctl -u giftbook -f           # 看日志
```

> `ExecStart` 里的 `python3` 路径按实际改：`which python3` 查一下，常见是 `/usr/bin/python3`。
> 改了 unit 文件后要 `sudo systemctl daemon-reload` 才生效。

### 5.3 端口小于 1024 时

如果想把端口设成 80 这类特权端口：

- 用 systemd 时加上 `AmbientCapabilities=CAP_NET_BIND_SERVICE`，或
- 干脆让 Python 监听 8094，再用 Nginx / Caddy 反代到 80（见文末[发布到公网](#八可选发布到公网)）

---

## 方式六：第三方 Docker 面板

如果你已经装了 Portainer、Dockge、1Panel 之类的面板，直接贴 compose。

| 面板 | 操作路径 |
|---|---|
| **Portainer** | Stacks → **Add stack** → Web editor 粘贴 → **Deploy the stack** |
| **Dockge** | 首页 **+ Compose** → 填名称与目录 → 粘贴 YAML → **Deploy** |
| **1Panel** | 容器 → **编排** → 创建编排 → 粘贴 → 确认 |

贴之前同样要保证 `server.py` 和 `src` 已存在于面板能访问的宿主机路径上（建议用绝对路径挂载）。

---

## 七、各平台路径速查

不同系统上「代码放哪、数据放哪」的惯例路径不一样，照抄下表可以少踩坑：

| 平台 | 建议代码目录 | 数据目录示例 |
|---|---|---|
| 通用 Linux | `/opt/gift-book-nas` | 同目录下 `data/` |
| 飞牛 fnOS | `/vol1/docker/gift-book-nas` | `/vol00/<盘名>/gift-book-data` |
| 群晖 DSM | `/volume1/docker/gift-book-nas` | `/volume1/docker/gift-book-nas/data` |
| 威联通 QNAP | `/share/Container/gift-book-nas` | 同左 |
| Unraid | `/mnt/user/appdata/gift-book-nas` | `/mnt/user/appdata/gift-book-nas/data` |
| TrueNAS SCALE | `/mnt/<池名>/apps/gift-book-nas` | 同左 |
| macOS / Windows | 自己选个目录 | 同目录下 `data/` |

---

## 八、可选：发布到公网

**默认只能局域网访问**，家里/公司内网用完全够了。要外网访问（比如婚礼现场在外面），有三种常见做法。

### 8.1 先把访问口令设上（重要）

**这一步不是可选的。** 一旦容器暴露到公网，任何知道地址的人都能读写你的礼簿，也能一键清库。

```yaml
environment:
  - GB_TOKEN=换成你自己的随机串
```

生成一个随机串：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(16))"
```

改完重启容器。之后：

- 首次打开会提示输入访问口令，输一次即记住
- 或者用带口令的链接免输入：`http://<地址>:8094/?token=<口令>`
- 也可以用请求头调用 API：`X-GB-Token: <口令>`

> 只是局域网自用、嫌麻烦？把 `GB_TOKEN` 留空即可，回到「打开就能用」。
> **但只要上公网，务必开启。**

### 8.2 三种外网访问方式

**① 内网穿透（最简单）**
用 frp / Cloudflare Tunnel / Tailscale 等把 8094 映射出去。注意必须已设置 `GB_TOKEN`。

**② 反向代理 + 域名 + HTTPS**
用 Nginx / Caddy / 群晖自带反向代理，把域名指到 `http://127.0.0.1:8094`，并配好证书。

Caddy 一行搞定（自动申请 HTTPS 证书）：

```
gift.example.com {
    reverse_proxy 127.0.0.1:8094
}
```

Nginx 片段：

```nginx
location / {
    proxy_pass http://127.0.0.1:8094;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

**③ 只在内网用，但给人一个「看起来像域名」的地址**
本地 DNS / hosts 指一下就行，不用暴露公网。

> 副屏 `http://<地址>:8094/guest-screen.html` 同样走这些通道，只要主页面能开，副屏就能开。

---

## 九、升级 / 备份 / 迁移 / 卸载

### 升级

```bash
cd /opt/gift-book-nas
git pull

# 方式一 / 方式四（挂载源码）
docker compose up -d --force-recreate

# 方式三（镜像部署，需要重新构建）
docker build -t gift-book-nas:latest . && docker compose up -d --force-recreate

# 方式五（裸机）
sudo systemctl restart giftbook
```

数据不受影响。升级前建议先备份（见下）。

**⚠️ 一个容易搞错的点**：本项目 compose 里没有 `build` 段，所以 `docker compose up -d --build` **不会重建容器**（实测输出 `Container xxx Running`，什么都没发生）。

因为代码是**挂载**进容器的，正确的做法是：

| 改了什么 | 怎么办 |
|---|---|
| `src/` 里的前端（html/js/css） | 浏览器强制刷新（Ctrl/Cmd + Shift + R），**不用重启容器** |
| `server.py` 后端 | `docker compose restart` |
| `docker-compose.yml` 配置 | `docker compose up -d --force-recreate` |
| `git pull` 拉了新代码 | `docker compose up -d --force-recreate`（最稳） |

### 备份

**只需要备份数据目录**（所有东西都在里面）：

```bash
# 方式一/三：数据目录
tar czf giftbook-backup-$(date +%Y%m%d).tar.gz -C /opt/gift-book-nas data/

# 或直接下载完整库文件
curl -o giftbook-db.json http://127.0.0.1:8094/api/export
```

也可以用 NAS 自带的快照 / 同步任务盯住数据目录，一劳永逸。

### 迁移到另一台机器

1. 新机器上按本指南部署好（先别启动）
2. 把旧机器的 `data/` 整个拷过去
3. 启动，完事

### 卸载

```bash
# Docker 方式
docker compose down            # 或 docker rm -f gift-book
docker rmi python:3-alpine     # 可选，清镜像

# 裸机方式
sudo systemctl disable --now giftbook
sudo rm /etc/systemd/system/giftbook.service
sudo systemctl daemon-reload
```

**数据目录不会被自动删除**，确认不需要了再手动删。

---

## 十、故障排查

| 现象 | 原因与解决 |
|---|---|
| 容器起不来，报 `Bind mount failed` / `no such file or directory` | 挂载的宿主机路径不存在。**群晖尤其常见**——目录不会自动创建，先在 File Station 建好再启动 |
| 页面打不开、连接被拒 | ① 容器没起来：`docker ps` 看一眼；② 端口被占：换 `ports` 左边数字；③ 防火墙 / 安全组没放行 |
| 打开是 404 或白屏 | `GB_STATIC_DIR` 指错了。容器内应为 `/home/static`，且 `src/index.html` 确实挂进去了 |
| 网页能开，但右下角显示「本地模式」（琥珀色） | 前端连不上后端 API。检查 `GB_STATIC_DIR` 是否指向 `src`、容器内 3000 是否在监听：`docker logs gift-book` |
| 数据没落到宿主机 | 忘了挂 `-v ...:/data`，或者 `GB_DATA_DIR` 和挂载点不一致。容器里 `/data` 应对应宿主机目录 |
| 页面提示要访问口令 | 你设置了 `GB_TOKEN`。用 `?token=<口令>` 进一次即可；不想要就把它清空后重启 |
| 改完前端不生效 | 浏览器缓存。强制刷新（Ctrl/Cmd + Shift + R）。方式三的镜像部署要重新 `docker build` |
| 容器状态一直是 `starting` / 反复重启 | `docker logs gift-book` 看报错。最常见是端口冲突和挂载路径问题 |
| 记了几百条之后变慢 | 属正常：每次写入都要重写整个 JSON 库文件。几百条完全无感；上万条建议换 SQLite |

**看日志是万能第一步：**

```bash
docker compose logs -f --tail=100     # Docker 方式
journalctl -u giftbook -n 100         # systemd 方式
```

---

## 附：一分钟决策

- **有 Docker，只想快点用** → [方式一](#方式一docker-compose推荐)
- **群晖 / 飞牛 / 威联通 / Unraid / TrueNAS** → [方式四](#方式四nas-图形面板点点点)（记得先建目录）
- **没有 Docker / 想跑在软路由上** → [方式五](#方式五裸机运行不用-docker)
- **想推镜像、多机统一分发** → [方式三](#方式三构建自包含镜像)
- **要外网访问** → 先看 [8.1 先把访问口令设上](#81-先把访问口令设上重要)
