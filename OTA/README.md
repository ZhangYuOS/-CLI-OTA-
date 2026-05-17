# 🏭 工业级 IoT OTA A/B 分区固件升级模拟器

> **Industrial-Grade IoT OTA A/B Partition Simulator**
>
> 一个面向物联网嵌入式设备的**客户端-服务端架构**固件 OTA 升级模拟系统。
> 支持 A/B 分区乒乓切换、SHA-256 固件校验、HTTP 断点续传、设备遥测上报，
> 以及看门狗触发的内核崩溃自动回滚——全部在本地 WSL / Linux 开发板上可运行。

---

## 🧱 核心架构

```
┌─────────────────────────┐       HTTP (LAN)       ┌──────────────────────────┐
│   Server (PC / WSL)     │ ◄─────────────────────► │   Client (开发板 / WSL)    │
│                         │                         │                          │
│  FastAPI                │   GET  /version         │  Click CLI + Rich TUI    │
│   ├─ /version           │   GET  /firmware        │   ├─ status              │
│   ├─ /firmware (Range)  │   POST /report          │   ├─ check               │
│   ├─ /report            │                         │   ├─ update (resumable)  │
│   └─ publish.py CLI     │                         │   ├─ rollback            │
│                         │                         │   ├─ slot_a/             │
│  release/               │                         │   ├─ slot_b/             │
│   ├─ manifest.json       │                         │   └─ device_status.json  │
│   └─ firmware_v*.bin    │                         │                          │
└─────────────────────────┘                         └──────────────────────────┘
```

| 层级 | 技术栈 | 职责 |
|------|--------|------|
| **Server** | FastAPI + Uvicorn | 固件版本管理、文件分发、遥测接收 |
| **Client** | Click + Requests + Rich | 固件下载、A/B 槽位管理、状态上报 |
| **通信** | HTTP REST (LAN) | 支持断点续传 (HTTP 206 Range) |

**物理部署场景：**
Server 运行在 PC/WSL 上（绑定 `0.0.0.0:8000`），Client 部署在同一台 WSL 或局域网内的
物理 Linux 开发板（如 RK3588）上，通过 `--server-url` 指定 PC 的 LAN IP 即可通信。

---

## 🔥 核心特性

### 1. A/B 分区乒乓机制 (Ping-Pong Slot Switching)

每次固件升级写入**备用槽位**，校验通过后才切换激活标志。天然支持多次
顺序升级的乒乓切换：

```
Slot A (v1.0.0) ──update──► Slot B (v2.0.0) ──update──► Slot A (v3.0.0)
```

### 2. SHA-256 固件防篡改校验

- 服务端在 `/version` 接口返回固件的 SHA-256 哈希值
- 客户端下载完成后**就地计算**完整文件的 SHA-256
- **仅在哈希完全匹配时**才切换激活槽位，任何不一致立即删除下载文件并中止

### 3. 断点续传 (HTTP 206 Range)

- 服务端 `/firmware` 支持 HTTP `Range` 头，返回 `206 Partial Content`
- 客户端在升级前检测备用槽是否存在**部分下载文件**
- 若存在，自动从断点处追加下载，Rich 进度条从实际偏移量开始渲染
- `Ctrl+C` 中断后**保留**部分文件，下次执行 `update` 自动续传

### 4. 设备遥测与云端上报

- 客户端内置 `report_to_server()`，在关键状态变更时 POST 到 `/report`
- 触发点：升级成功、手动/自动回滚、下载中断、哈希校验失败
- 服务端实时打印 `[TELEMETRY]` 日志，便于监控和调试

### 5. 看门狗自动回滚 (Watchdog Auto-Rollback)

- `update --simulate-boot-crash` 模拟升级后内核崩溃
- 系统完成完整升级流程后立即触发 `KERNEL PANIC`
- **自动执行回滚**到上一个稳定槽位，证明看门狗恢复路径

### 6. 多版本固件管理

- `server/publish.py` 一键生成并注册新版本固件
- `manifest.json` 维护所有可用版本的元数据索引
- 客户端可通过 `--target-version` 安装**任意历史版本**
- `unpublish` 命令支持删除最新或指定版本，无需重启服务端

---

## 🛠 环境准备

### 依赖安装

```bash
pip install -r requirements.txt
```

**`requirements.txt`：**

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
click>=8.1.7
requests>=2.31.0
rich>=13.7.0
```

### 目录结构

```
OTA/
├── server/
│   ├── main.py              # FastAPI 服务端入口
│   ├── publish.py           # 固件发布/删除 CLI
│   └── release/
│       ├── manifest.json    # 版本清单
│       └── firmware_v*.bin  # 固件二进制文件
├── client/
│   ├── cli.py               # 设备端 CLI 工具
│   ├── device_status.json   # 本地槽位状态文件
│   ├── slot_a/              # A 槽固件目录
│   └── slot_b/              # B 槽固件目录
├── docs/
│   └── AI_Prompt_Log.md     # 开发过程日志
├── requirements.txt
└── README.md
```

---

## 📖 使用指南

### 一、启动服务端

```bash
# 方式一：直接运行（推荐）
python server/main.py

# 方式二：使用 uvicorn
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

> **⚠️ 重要：** 必须绑定 `0.0.0.0`，否则物理开发板无法通过 LAN IP 访问。

启动后访问 `http://127.0.0.1:8000/version` 验证：

```json
{
    "latest_version": "2.0.0",
    "firmware": "firmware_v2.0.0.bin",
    "size": 5242880,
    "sha256": "c036cbb7553a909f8b8877d4461924307f27ecb66cff928eeeafd569c3887e29"
}
```

### 二、发布新固件

```bash
# 生成并注册 v3.0.0
python server/publish.py publish 3.0.0

# 删除最新版本
python server/publish.py unpublish --latest

# 删除指定版本
python server/publish.py unpublish 3.0.0
```

> 无需重启服务端——`manifest.json` 在每次请求时从磁盘实时加载。

### 三、客户端操作

```bash
# 查看当前 A/B 槽位状态（本地）
python client/cli.py status

# 检查服务器是否有新固件（查最新版）
python client/cli.py check

# 检查指定版本
python client/cli.py check --target-version 2.0.0

# 执行 OTA 升级
python client/cli.py update

# 升级到指定版本
python client/cli.py update --target-version 2.0.0

# 升级后模拟内核崩溃（看门狗自动回滚）
python client/cli.py update --simulate-boot-crash

# 手动回滚到备用槽位
python client/cli.py rollback
```

**物理开发板使用：**

```bash
# 将 client/ 目录复制到开发板，运行时指定 PC 的 LAN IP
python client/cli.py --server-url http://192.168.1.50:8000 status
python client/cli.py --server-url http://192.168.1.50:8000 check
python client/cli.py --server-url http://192.168.1.50:8000 update
```

#### 参数速查

| 参数 | 适用命令 | 说明 | 默认值 |
|------|----------|------|--------|
| `--server-url` | 全部 | OTA 服务器地址 | `http://127.0.0.1:8000` |
| `--target-version` | `check`, `update` | 指定目标版本，不传则取最新 | 无 |
| `--simulate-boot-crash` | `update` | 升级成功后触发模拟崩溃并自动回滚 | `False` |

---

## 🧪 高级特性测试

### 断点续传测试

```bash
# 1. 开始升级
python client/cli.py update

# 2. 在下载过程中按 Ctrl+C 中断
#    → 提示 "Download Aborted. Partial file kept."
#    → 提示 "Run update again to resume."

# 3. 再次执行升级——自动从断点续传
python client/cli.py update
#    → 显示 "Resuming from byte 1,048,576 (20%)"
#    → "Server accepted Range request (206 Partial Content)."
#    → 进度条从 20% 开始
```

### 看门狗自动回滚测试

```bash
# 假设当前状态：Slot A 活跃 v2.0.0，Slot B 为空

# 1. 先升级到 v3.0.0
python client/cli.py update
#    → Ping-Pong: Slot A(v2.0.0) → Slot B(v3.0.0)

# 2. 发布新版本
python server/publish.py publish 4.0.0

# 3. 升级并模拟内核崩溃
python client/cli.py update --simulate-boot-crash
#    → Ping-Pong: Slot B(v3.0.0) → Slot A(v4.0.0)
#    → "✓ Update successful!"
#    → "[Simulating Reboot...] → KERNEL PANIC! Watchdog triggered."
#    → "✓ Auto-rollback complete. Active slot restored to B (v3.0.0)"

# 4. 验证恢复到了之前的稳定版本
python client/cli.py status
#    → Slot B 活跃 v3.0.0
```

### 遥测验证

```bash
# 服务端控制台会实时打印遥测日志：
# [TELEMETRY] device=DEV-RK3588-001  slot=B  version=3.0.0  status=Update Successful
# [TELEMETRY] device=DEV-RK3588-001  slot=B  version=3.0.0  status=Rollback Triggered
```

---

## 🔒 安全设计原则

- **零信任校验：** 固件哈希不匹配 → 立即删除文件 → **绝不**切换激活槽位
- **下载中断保护：** 中断后保留部分文件，不污染设备状态 JSON
- **看门狗恢复：** 即使"升级成功"，内核崩溃后自动回退到上一个已知良好版本
- **遥测静默失败：** 遥测上报失败不影响核心升级流程

---

## 📋 API 参考

| Method | Endpoint | 说明 |
|--------|----------|------|
| `GET` | `/version` | 获取固件版本元数据（支持 `?v=` 查询指定版本） |
| `GET` | `/firmware` | 下载固件（支持 `Range` 头断点续传，返回 `206`） |
| `POST` | `/report` | 接收设备遥测数据 |
| `GET` | `/docs` | FastAPI 自动生成的 Swagger UI |

---

## 📝 开发日志

完整的分步开发记录、每步的设计决策和 bug 修复过程，见
[`docs/AI_Prompt_Log.md`](docs/AI_Prompt_Log.md)。

---

*Built with FastAPI, Click, Requests & Rich — for IoT OTA education and demonstration.*
