<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/brand/mark-dark.svg"><img src="assets/brand/mark.svg" alt="LiliuxFlow lily mark" width="96" height="96"></picture></p>
<h1 align="center">LiliuxFlow</h1>
<p align="center">本地 LLM 部署与服务管理平台</p>
<p align="center">
  <a href="https://github.com/Hiruynk/LiliuxFlow/releases"><img alt="version / v0.1.0" src="https://img.shields.io/badge/version-v0.1.0-2563eb?style=flat-square"></a>
  <a href="#requirements"><img alt="macOS / 27.0" src="https://img.shields.io/badge/macOS-27.0-4c4e75?style=flat-square&amp;logo=apple&amp;logoColor=white"></a>
  <a href="#requirements"><img alt="Apple Silicon / M5+" src="https://img.shields.io/badge/Apple_Silicon-M5%2B-e7883b?style=flat-square&amp;logo=apple&amp;logoColor=white"></a>
  <a href="LICENSE"><img alt="license / Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square"></a>
</p>

<p align="center"><a href="README.md">English</a> · <a href="README.zh-Hant.md">繁體中文</a> · <strong>简体中文</strong> · <a href="README.ja.md">日本語</a></p>

## 关于本项目

LiliuxFlow 是面向 Apple Silicon 的本地 LLM 部署与服务管理平台，将 Lily 推理、LiteLLM 的 API 访问管理和 llama-swap 的模型生命周期管理整合为一套原生服务，提供多语言管理界面及现有应用的 API 兼容层。它适合希望将应用接入本地模型，并在 Mac 上管理整套服务的开发者。

LiliuxFlow 为这些组件补充了服务编排、感知请求状态的生命周期保护和协议适配。一套流程即可准备原生运行环境，为各应用创建独立 API 密钥，协调模型加载与请求取消，并完成日常维护。管理界面由 LiteLLM 提供，模型进程由 llama-swap 管理，推理由 Lily 执行。

## 功能亮点

- **原生部署。** 使用原生 Rust／Metal、Python、Go 和 PostgreSQL 构建并运行服务。macOS 用户级 LaunchAgent 负责监督服务；每个安装实例都有专用目录，用于保存配置、凭据及运行数据。
- **应用 API 访问管理。** 通过 LiteLLM 的虚拟密钥、模型权限、用量跟踪和速率限制，管理各应用的访问。Chat Completions 客户端只需配置本地 API 地址、模型名称及自己的密钥。
- **根据请求状态保护模型生命周期。** llama-swap 按需加载模型，并在空闲一段时间后卸载。LiliuxFlow 的生命周期守卫协调请求接入与管理操作，在推理进行时拒绝手动卸载。
- **协议兼容与取消传递。** 可使用兼容 OpenAI 的 Chat Completions 或现有自定义聊天接口。LiliuxFlow 适配器保留纯文本响应、thinking／content SSE 及调用方身份，并沿服务链传递客户端断开连接后的取消。
- **四种界面语言。** 首页与管理界面提供英语、繁体中文、简体中文和日语。切换管理界面语言时保留表单与正在进行的流式响应；API 字段、模型名称及用户输入保持原值。首页另有独立的明暗主题偏好。
- **日常运维。** 使用随附的原生工具查看安装状态、诊断运行条件、安全停止服务，以及备份或恢复安装数据。不同数据目录可用于预先准备升级实例，再切换服务。

## 界面预览

![LiliuxFlow 深色首页](assets/screenshots/welcome-dark.jpg)

英文深色首页介绍服务，并提供 API 控制台和参考文档入口。

![LiliuxFlow 模型管理](assets/screenshots/model-management.jpg)

llama-swap 模型管理界面列出 64K、128K 和 262K 上下文配置；画面中的三档均未加载。

## 组件如何协作

```text
应用
    │
    ├── 兼容 OpenAI 的 Chat Completions ─────┐
    └── 现有聊天 API → LiliuxFlow Compat ────┤
                                            ▼
                                         LiteLLM ── PostgreSQL
                                            │
                                  LiliuxFlow 生命周期守卫
                                            │
                                        llama-swap
                                            │
                                           Lily

LiliuxFlow 原生工具 → 安装／launchd／日常运维
```

| 层级 | 提供者 | LiliuxFlow 的集成工作 |
| --- | --- | --- |
| 推理 | [Lily](https://github.com/fabiogreter/lily-qwen3.8-flash-next) | 原生部署、受控服务配置与服务层补丁 |
| 模型进程 | [llama-swap](https://github.com/mostlygeek/llama-swap) | Lily 子进程包装、请求状态守卫及 token 指标集成 |
| API 管理 | [LiteLLM](https://github.com/BerriAI/litellm) | 模型路由、调用方密钥配置及多语言管理界面集成 |
| 兼容与运维 | LiliuxFlow | 自定义协议适配、跨层取消、安装和维护工具 |

LiliuxFlow 的自有实现包括安装监督程序、模型运行器、生命周期守卫及兼容适配器。Lily 集成还处理日志脱敏、尽早响应取消，以及取消后的 session 缓存处理。这些能力构建在上游的推理与管理功能之上。各组件的职责详见[架构文档](docs/ARCHITECTURE.md)。

<a id="requirements"></a>

## 系统要求

| 项目 | 必要条件与实测基准 |
| --- | --- |
| 主机 | macOS arm64（Apple Silicon）；实测基准为配备 Apple M5 Max、128 GiB 统一内存的 Mac Studio，系统为 macOS 27.0 |
| 原生工具 | uv、Rust 1.97.0、PostgreSQL 17；以下命令使用 Python 3.12 |
| 模型 | Qwen3.8-Flash-Next Lily Q4；需单独下载，约 98 GiB |
| 工作空间 | 模型之外，至少另留 64 GiB 可用构建空间，并为运行缓存预留容量 |
| 推理配置 | 输入与输出总计 65,536 tokens、checkpoint 默认 high thinking、QSA Split、禁用 MTP |

其他 Apple Silicon 配置尚未验证。兼容的 macOS arm64 主机无需匹配实测芯片或内存容量即可继续；硬件差异和 RAM 余量不足只会产生提醒。请为模型及其上下文缓存保留足够的可用内存。源码构建工具会获取固定版本的 Go 和 Node，用于原生程序及静态 UI 构建。准备方法详见[安装文档](docs/INSTALLATION.md)，资源监测详见[运维文档](docs/OPERATIONS.md)。

## 上下文配置

| 模型 | 总上下文 | 可用状态 |
| --- | ---: | --- |
| `qwen3.8-flash-next-lily-q4-64k` | 65,536 tokens | 已启用；默认模型 |
| `qwen3.8-flash-next-lily-q4-128k` | 131,072 tokens | 已启用；可选 |
| `qwen3.8-flash-next-lily-q4-262k` | 262,144 tokens | 已启用；可选 |
| `qwen3.8-flash-next-lily-q4-mtp2-64k` | 65,536 tokens | 可选；须显式授权 |
| `qwen3.8-flash-next-lily-q4-mtp2-128k` | 131,072 tokens | 停用；等待该配置验收 |
| `qwen3.8-flash-next-lily-q4-mtp2-262k` | 262,144 tokens | 停用；等待该配置验收 |

上述三个 MTP0 配置共用同一份外置 Q4 checkpoint，保留 checkpoint 默认 high thinking、QSA Split 和 MTP0。输入与输出共同受总上下文上限约束。应用使用长上下文模型前，须为其虚拟密钥明确授予该模型的访问权限。请求采用有界队列；切换配置会等待当前生成结束并完成资源释放。

### 可选的 MTP2 64K 配置

`qwen3.8-flash-next-lily-q4-mtp2-64k` 是 `ctx64k-mtp2` 的显式可选别名。它共用 Q4 checkpoint，保留 HIGH thinking；总上下文和包含推理的输出预算均为 65,536 tokens。默认模型与原有三个 MTP0 别名保持不变。

在已停止的安装中，使用“快速开始”的 `lf` 函数执行 `lf build --optin-engine latest13f-defer-pc123-mtp2-opt64k --ui-manifest manifests/distribution/ui-recipe-context-profiles.json --execute`，显式选择此引擎。`lf profiles list` 和 `lf profiles info ctx64k-mtp2` 只读取配置目录，不加载模型。

启动后，可用 `lf profiles grant-owner ctx64k-mtp2 --dry-run` 预览指定 owner caller 的授权，再在需要时加上 `--execute` 追加此别名。其他 caller 须分别显式获授权；空白或通配授权不会启用此模型。每次请求须使用精确别名，不能通过 body 修改引擎、路径、内存、KV 精度或 context。JSON／JSON Schema 响应格式和工具执行仍不支持。

三个 MTP2 配置共用同一引擎和 Q4 checkpoint，输出上限均为 65,536 tokens，包含推理。MTP2 128K／262K 初始停用，须分别完成配置验收和可信启用；启用 128K 不会同时启用 262K。`lf profiles grant-owner ctx128k-mtp2 --dry-run` 仅适用于已启用的配置；262K 请使用对应的精确 ID。

## 相同工作负载的性能

> [!NOTE]
> 实测使用同一份 Q4 checkpoint、MTP0、QSA Split、checkpoint 默认 high thinking 和温度 0：4,096 个未命中缓存的输入 token、256 个输出 token，各配置交错测试三次。数值为中位数；prefill 范围反映各次测试的波动。这组相同的 4K 工作负载不代表接近上下文上限时的吞吐量。

| 模型 | Prefill（tokens/s） | Prefill 范围 | Decode（tokens/s） |
| --- | ---: | ---: | ---: |
| 64K | 1545.82 | 1379.54–1733.04 | 90.35 |
| 128K | 1347.47 | 1284.26–1806.12 | 87.72 |
| 262K | 1607.22 | 1592.39–1619.80 | 90.47 |

长上下文质量另用不同测试数据验证：128K 输入最高达 126,464 个 token，262K 最高达 257,536 个 token。切换配置或恢复冷缓存会增加加载时间；后续缓存请求的成本不同。API 默认仍为 64K 配置。

## 快速开始

> [!IMPORTANT]
> 模型为可选安装项。你可以使用现有的 Lily 格式 checkpoint、下载 manifest 指定的模型，或先安装管理服务，稍后再添加模型。模型权重不包含在本仓库中。

| 可选模型 | 格式 | 下载大小 | 来源与许可 |
| --- | --- | --- | --- |
| Qwen3.8-Flash-Next | Lily Q4 | 约 98.3 GiB | [上游模型](https://huggingface.co/fabiogreter/Qwen3.8-Flash-Next-lily-q4) · [Qwen Community License 1.0](docs/productization/MODEL_LICENSE.txt) |

### 获取源码

通过 Git 克隆本仓库，或解压包含 `SOURCE_COMMIT.json` 的 LiliuxFlow 官方源码发行附件：

```sh
git clone https://github.com/Hiruynk/LiliuxFlow.git
cd LiliuxFlow
```

GitHub 自动生成的 **Source code (zip/tar.gz)** 压缩包缺少这份来源记录，无法直接用于默认构建流程。源码与安装方法详见[安装文档](docs/INSTALLATION.md)。

若使用现有模型，请将 `MODEL_DIR` 指向完整的 checkpoint 目录；setup 会直接使用该目录。

在仓库根目录运行：

```sh
export MODEL_DIR="$HOME/Models/Qwen3.8-Flash-Next-lily-q4"

# 仅供当前 shell 使用的辅助函数。
lf() {
  uv run --no-project --python 3.12 python scripts/distribution/liliuxflow.py "$@"
}

lf setup --model-dir "$MODEL_DIR"
lf build --ui-manifest manifests/distribution/ui-recipe-context-profiles.json --execute
lf verify-model
lf initialize --execute
lf doctor
lf start
lf status
```

> [!TIP]
> 该流程会准备运行环境、验证已下载的模型文件、初始化安装数据库、检查就绪状态，并启动本地服务。模型按需加载：服务正常且空闲时，模型可能尚未驻留内存，因此第一次模型请求会包含加载时间。

`lf models list` 和 `lf models info qwen3.8-flash-next-lily-q4` 可离线查看模型目录。若要下载模型，请明确执行安装流程：

```sh
mkdir -p "$(dirname "$MODEL_DIR")"
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --dry-run
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --execute
```

`--dry-run` 会连接 Hugging Face 获取下载元数据；`--execute` 才会下载模型文件。若要先安装管理服务，请用 `lf setup --without-model` 替换上方指定模型目录的 setup，并跳过 `lf verify-model`。管理服务可以先行启用；准备提供推理时，再添加并验证模型。稍后添加模型的方法详见[安装文档](docs/INSTALLATION.md)。

| 用途 | 默认入口 |
| --- | --- |
| API 管理 | <http://127.0.0.1:4000/ui/> |
| 模型管理 | <http://127.0.0.1:8080/ui/> |
| Chat Completions API base | `http://127.0.0.1:4000/v1` |

### 打开管理界面

默认数据目录为 `~/Library/Application Support/LiliuxFlow`。请在本机读取其中的 `secrets/bootstrap.json` 获取管理登录信息，从 `secrets/caller.json` 获取安装实例的应用密钥及连接配置。重复执行相同 setup 会保留已有配置与凭据。所有服务仅监听 loopback；如果默认端口已被占用，请在 setup 时选择其他端口。

<a id="connect-an-application"></a>

## 接入应用

随附客户端会读取该安装实例的调用方配置，并流式显示 Chat Completions 响应：

```sh
uv run --no-project --python 3.12 python scripts/distribution/client_example.py --api openai --stream
```

其他客户端请将 API base 设为 `http://127.0.0.1:4000/v1`、模型设为 **`qwen3.8-flash-next-lily-q4-64k`**，并使用分配给该应用的 LiteLLM 虚拟密钥。在 API 管理界面配置模型权限与限制。默认与最大输出预算均为 65,536 tokens，包含 thinking。输入和输出共享所选模型的总 context；Lily 会将输出限制在剩余空间，达到上限时返回 `length`。

此配置提供 Chat Completions。现有接口使用自定义 SSE 或纯文本；适配客户端时应遵循其传输格式。不支持 JSON／JSON Schema 响应格式、音频／视频或每个请求的 `keep_alive`。现有 API 示例、支持选项和取消行为详见 [API 文档](docs/API.md)。

## 文档与开发

- [安装](docs/INSTALLATION.md)：前置要求、模型准备及独立安装实例。
- [API](docs/API.md)：客户端配置、现有协议及支持的请求选项。
- [运维](docs/OPERATIONS.md)：状态、日志、备份、恢复和升级。
- [架构](docs/ARCHITECTURE.md)：上游组件与 LiliuxFlow 的集成。
- [可选 Cloudflare Edge](docs/cloudflare-edge.md)：Worker 静态资源，以及通过限定范围的 VPC Service 访问私有后端。
- [第三方声明](THIRD_PARTY_NOTICES.md)：组件许可及来源署名。

日常检查可运行 `lf status` 或 `lf doctor`；停止安装实例请使用 `lf stop`。贡献应保留调用方身份、取消行为、传输协议兼容性和上游署名。原生组件的变更应能通过随附的来源清单与构建配方复现。

## 许可与致谢

LiliuxFlow 的自有代码、文档及项目自有资产采用 [Apache License 2.0](LICENSE)。第三方组件保留各自许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [NOTICE](NOTICE)。模型权重需单独下载，并受其[模型许可](docs/productization/MODEL_LICENSE.txt)约束。

LiliuxFlow 构建在 Lily、LiteLLM 和 llama-swap 之上，发行内容保留其作者署名与许可声明。
