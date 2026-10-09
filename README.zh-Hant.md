<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/brand/mark-dark.svg"><img src="assets/brand/mark.svg" alt="LiliuxFlow lily mark" width="96" height="96"></picture></p>
<h1 align="center">LiliuxFlow</h1>
<p align="center">本地 LLM 部署與服務管理平台</p>
<p align="center">
  <a href="https://github.com/Hiruynk/LiliuxFlow/releases"><img alt="version / v0.1.0" src="https://img.shields.io/badge/version-v0.1.0-2563eb?style=flat-square"></a>
  <a href="#requirements"><img alt="macOS / 27.0" src="https://img.shields.io/badge/macOS-27.0-4c4e75?style=flat-square&amp;logo=apple&amp;logoColor=white"></a>
  <a href="#requirements"><img alt="Apple Silicon / M5+" src="https://img.shields.io/badge/Apple_Silicon-M5%2B-e7883b?style=flat-square&amp;logo=apple&amp;logoColor=white"></a>
  <a href="LICENSE"><img alt="license / Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square"></a>
</p>

<p align="center"><a href="README.md">English</a> · <strong>繁體中文</strong> · <a href="README.zh-Hans.md">简体中文</a> · <a href="README.ja.md">日本語</a></p>

## 關於本專案

LiliuxFlow 是面向 Apple Silicon 的本地 LLM 部署與服務管理平台，將 Lily 推理、LiteLLM 的 API 存取管理，以及 llama-swap 的模型生命週期管理整合為一套原生服務，提供多語管理介面與既有應用的 API 相容層。它適合希望將應用程式連接至本地模型，並在 Mac 上管理整套服務的開發者。

LiliuxFlow 為這些元件補上服務編排、感知請求狀態的生命週期保護，以及協議適配。透過一套流程，即可準備原生執行環境、為各應用程式建立獨立 API 金鑰、協調模型載入與請求取消，並進行日常維護。管理介面由 LiteLLM 提供，模型程序由 llama-swap 管理，推理由 Lily 執行。

## 功能重點

- **原生部署。** 使用原生 Rust／Metal、Python、Go 與 PostgreSQL 建置並運行服務。macOS 使用者層級 LaunchAgent 負責監督服務；每個安裝實例都有專用目錄，保存設定、憑證與執行資料。
- **應用程式 API 存取管理。** 透過 LiteLLM 的虛擬金鑰、模型權限、用量追蹤與速率限制，管理各應用程式的存取。Chat Completions 用戶端只需設定本地 API 位址、模型名稱與自己的金鑰。
- **依請求狀態保護模型生命週期。** llama-swap 按需載入模型，並在閒置一段時間後卸載。LiliuxFlow 的生命週期守衛協調請求接入與管理操作，推理進行中會拒絕手動卸載。
- **協議相容與取消傳遞。** 可使用 OpenAI 相容的 Chat Completions 或既有自訂聊天介面。LiliuxFlow 適配器保留純文字回應、thinking／content SSE 與呼叫者身分，並將用戶端斷線取消沿服務鏈傳遞。
- **四語介面。** 首頁與管理介面提供英文、繁體中文、簡體中文及日文。切換管理介面語言時會保留表單與進行中的串流；API 欄位、模型名稱與使用者輸入維持原值。首頁另有獨立的亮／暗模式偏好。
- **日常維護。** 使用隨附的原生工具查看安裝狀態、診斷必要條件、安全停止服務，以及備份或還原安裝資料。不同資料目錄可用來預先準備升級實例，再切換服務。

## 畫面預覽

![LiliuxFlow 暗色首頁](assets/screenshots/welcome-dark.jpg)

英文暗色首頁介紹服務，並提供 API 控制台與參考文件入口。

![LiliuxFlow 模型管理](assets/screenshots/model-management.jpg)

llama-swap 模型管理介面列出 64K、128K 與 262K 上下文設定檔；畫面中的三檔皆未載入。

## 元件如何協作

```text
應用程式
    │
    ├── OpenAI 相容 Chat Completions ────────┐
    └── 既有聊天 API → LiliuxFlow Compat ────┤
                                            ▼
                                         LiteLLM ── PostgreSQL
                                            │
                                  LiliuxFlow 生命週期守衛
                                            │
                                        llama-swap
                                            │
                                           Lily

LiliuxFlow 原生工具 → 安裝／launchd／日常維護
```

| 層級 | 提供者 | LiliuxFlow 的整合工作 |
| --- | --- | --- |
| 推理 | [Lily](https://github.com/fabiogreter/lily-qwen3.8-flash-next) | 原生部署、受控服務設定與服務層修補 |
| 模型程序 | [llama-swap](https://github.com/mostlygeek/llama-swap) | Lily 子程序包裝、請求狀態守衛與 token 指標整合 |
| API 管理 | [LiteLLM](https://github.com/BerriAI/litellm) | 模型路由、呼叫者金鑰設定與多語管理介面整合 |
| 相容與維護 | LiliuxFlow | 自訂協議適配、跨層取消、安裝與維護工具 |

LiliuxFlow 的自有實作包含安裝監督程序、模型執行器、生命週期守衛與相容適配器。Lily 整合也涵蓋日誌遮蔽、較早處理取消，以及取消後的 session 快取處理。這些功能建構於上游的推理與管理能力之上；各元件職責詳見[架構文件](docs/ARCHITECTURE.md)。

<a id="requirements"></a>

## 系統需求

| 項目 | 必要條件與實測基準 |
| --- | --- |
| 主機 | macOS arm64（Apple Silicon）；實測基準為 Apple M5 Max、128 GiB 統一記憶體的 Mac Studio，系統為 macOS 27.0 |
| 原生前置工具 | uv、Rust 1.97.0、PostgreSQL 17；下列指令選用 Python 3.12 |
| 模型 | Qwen3.8-Flash-Next Lily Q4；需另外下載，約 98 GiB |
| 工作空間 | 模型以外，另須至少 64 GiB 可用建置空間，並為執行快取預留容量 |
| 推理設定 | 輸入與輸出合計 65,536 tokens、checkpoint 預設 high thinking、QSA Split、停用 MTP |

其他 Apple Silicon 配置尚未驗證。相容的 macOS arm64 主機無須符合實測晶片或記憶體容量即可繼續；硬體差異與 RAM 餘裕不足只會產生提醒。請為模型及其上下文快取保留足夠的可用記憶體。原始碼建置工具會取得固定版本的 Go 與 Node，供原生程式與靜態 UI 建置使用。準備方式詳見[安裝文件](docs/INSTALLATION.md)，資源觀測詳見[維護文件](docs/OPERATIONS.md)。

## 上下文設定檔

| 模型 | 總上下文 | 可用狀態 |
| --- | ---: | --- |
| `qwen3.8-flash-next-lily-q4-64k` | 65,536 tokens | 已啟用；預設模型 |
| `qwen3.8-flash-next-lily-q4-128k` | 131,072 tokens | 已啟用；選用 |
| `qwen3.8-flash-next-lily-q4-262k` | 262,144 tokens | 已啟用；選用 |

上述三個設定檔共用同一份外置 Q4 checkpoint，保留 checkpoint 預設 high thinking、QSA Split 與 MTP0。輸入及輸出共同受總上下文上限約束。應用程式使用長上下文模型前，須為其虛擬金鑰明確授予該模型的存取權。請求採有界佇列；切換設定檔會等待目前生成結束並完成資源釋放。

### 選用的 MTP2 64K 設定檔

`qwen3.8-flash-next-lily-q4-mtp2-64k` 是 `ctx64k-mtp2` 的明確選用別名。它共用 Q4 checkpoint，保留 HIGH thinking；總上下文及包含推理的輸出預算均為 65,536 tokens。預設模型及原有三個 MTP0 別名維持不變。

在已停止的安裝中，使用「快速開始」的 `lf` 函式執行 `lf build --optin-engine latest13f-defer-pc123-mtp2-opt64k --ui-manifest manifests/distribution/ui-recipe-context-profiles.json --execute`，明確選用此引擎。`lf profiles list` 與 `lf profiles info ctx64k-mtp2` 只讀取設定檔目錄，不載入模型。

啟動後，可用 `lf profiles grant-owner ctx64k-mtp2 --dry-run` 預覽指定 owner caller 的授權，再於需要時加上 `--execute` 附加此別名。其他 caller 須各自明確獲授權；空白或萬用授權不會啟用此模型。每次請求須選用精確別名，不能透過 body 改引擎、路徑、記憶體、KV 精度或 context。JSON／JSON Schema 回應格式及工具執行仍不支援。

## 相同工作負載的效能

> [!NOTE]
> 實測使用同一份 Q4 checkpoint、MTP0、QSA Split、checkpoint 預設 high thinking 與溫度 0：4,096 個未命中快取的輸入 token、256 個輸出 token，各設定檔交錯測試三次。數值為中位數；prefill 範圍反映各次測試的波動。這組相同的 4K 工作負載不代表接近上下文上限時的吞吐量。

| 模型 | Prefill（tokens/s） | Prefill 範圍 | Decode（tokens/s） |
| --- | ---: | ---: | ---: |
| 64K | 1545.82 | 1379.54–1733.04 | 90.35 |
| 128K | 1347.47 | 1284.26–1806.12 | 87.72 |
| 262K | 1607.22 | 1592.39–1619.80 | 90.47 |

長上下文品質另以不同測試資料驗證：128K 輸入最高達 126,464 個 token，262K 最高達 257,536 個 token。切換設定檔或還原冷快取會增加載入時間；後續快取請求的成本不同。API 預設仍為 64K 設定檔。

## 快速開始

> [!IMPORTANT]
> 模型為選裝項目。你可以使用現有 Lily 格式 checkpoint、下載 manifest 指定的模型，或先安裝管理服務，稍後再加入模型。模型權重不包含在本倉庫中。

| 選裝模型 | 格式 | 下載大小 | 來源與授權 |
| --- | --- | --- | --- |
| Qwen3.8-Flash-Next | Lily Q4 | 約 98.3 GiB | [上游模型](https://huggingface.co/fabiogreter/Qwen3.8-Flash-Next-lily-q4) · [Qwen Community License 1.0](docs/productization/MODEL_LICENSE.txt) |

### 取得原始碼

以 Git 複製本倉庫，或解壓含有 `SOURCE_COMMIT.json` 的 LiliuxFlow 官方原始碼發行附件：

```sh
git clone https://github.com/Hiruynk/LiliuxFlow.git
cd LiliuxFlow
```

GitHub 自動產生的 **Source code (zip/tar.gz)** 壓縮包缺少此來源記錄，無法直接用於預設建置流程。原始碼與安裝方式詳見[安裝文件](docs/INSTALLATION.md)。

若使用現有模型，請將 `MODEL_DIR` 指向完整 checkpoint 目錄；setup 會直接使用該目錄。

在倉庫根目錄執行：

```sh
export MODEL_DIR="$HOME/Models/Qwen3.8-Flash-Next-lily-q4"

# 僅供目前 shell 使用的輔助函式。
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
> 此流程會準備執行環境、驗證已下載的模型檔案、初始化安裝資料庫、檢查就緒狀態並啟動本地服務。模型採按需載入：服務健康且閒置時，模型可能尚未常駐記憶體，因此第一次模型請求會包含載入時間。

`lf models list` 與 `lf models info qwen3.8-flash-next-lily-q4` 可離線查看模型目錄。若要下載模型，請明確執行安裝流程：

```sh
mkdir -p "$(dirname "$MODEL_DIR")"
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --dry-run
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --execute
```

`--dry-run` 會連線至 Hugging Face 取得下載中繼資料；`--execute` 才會下載模型檔案。若要先安裝管理服務，請用 `lf setup --without-model` 取代上方指定模型目錄的 setup，並略過 `lf verify-model`。管理服務可先行啟用；準備提供推理時，再加入並驗證模型。稍後加入模型的方式詳見[安裝文件](docs/INSTALLATION.md)。

| 用途 | 預設入口 |
| --- | --- |
| API 管理 | <http://127.0.0.1:4000/ui/> |
| 模型管理 | <http://127.0.0.1:8080/ui/> |
| Chat Completions API base | `http://127.0.0.1:4000/v1` |

### 開啟管理介面

預設資料目錄為 `~/Library/Application Support/LiliuxFlow`。請在本機閱讀其中的 `secrets/bootstrap.json` 取得管理登入資料，並從 `secrets/caller.json` 取得安裝實例的應用程式金鑰與連線設定。重跑相同 setup 會保留已有設定及憑證。所有服務僅監聽 loopback；若預設連接埠已被占用，請在 setup 時選擇其他連接埠。

<a id="connect-an-application"></a>

## 連接應用程式

隨附用戶端會讀取此安裝實例的呼叫者設定，並串流顯示 Chat Completions 回應：

```sh
uv run --no-project --python 3.12 python scripts/distribution/client_example.py --api openai --stream
```

其他用戶端請設定 API base 為 `http://127.0.0.1:4000/v1`、模型為 **`qwen3.8-flash-next-lily-q4-64k`**，並使用分配給該應用程式的 LiteLLM 虛擬金鑰。在 API 管理介面設定其模型權限與限制。預設與最大輸出預算均為 65,536 tokens，包含 thinking。輸入與輸出共用所選模型的總 context；Lily 會將輸出限制在剩餘空間，達到上限時回報 `length`。

此設定提供 Chat Completions。既有介面採用自訂 SSE 或純文字；調整用戶端時應依照其傳輸格式。不支援 JSON／JSON Schema 回應格式、音訊／視訊或逐請求 `keep_alive`。既有 API 範例、支援選項及取消行為詳見 [API 文件](docs/API.md)。

## 文件與開發

- [安裝](docs/INSTALLATION.md)：前置需求、模型準備與獨立安裝實例。
- [API](docs/API.md)：用戶端設定、既有協議與支援的請求選項。
- [維護](docs/OPERATIONS.md)：狀態、日誌、備份、還原與升級。
- [架構](docs/ARCHITECTURE.md)：上游元件與 LiliuxFlow 的整合。
- [選用 Cloudflare Edge](docs/cloudflare-edge.md)：Worker 靜態資產，以及透過限定範圍的 VPC Service 存取私有後端。
- [第三方聲明](THIRD_PARTY_NOTICES.md)：元件授權與來源署名。

日常檢查可執行 `lf status` 或 `lf doctor`；停止安裝實例請使用 `lf stop`。貢獻應保留呼叫者身分、取消行為、傳輸協議相容性與上游署名。原生元件變更應能透過隨附的來源清單及建置配方重現。

## 授權與致謝

LiliuxFlow 的自有程式碼、文件及專案自有資產採用 [Apache License 2.0](LICENSE)。第三方元件保留各自授權，詳見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 與 [NOTICE](NOTICE)。模型權重需另外下載，並受其[模型授權](docs/productization/MODEL_LICENSE.txt)約束。

LiliuxFlow 建構於 Lily、LiteLLM 與 llama-swap 之上，發行內容保留其作者署名與授權聲明。
