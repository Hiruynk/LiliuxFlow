<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/brand/mark-dark.svg"><img src="assets/brand/mark.svg" alt="LiliuxFlow lily mark" width="96" height="96"></picture></p>
<h1 align="center">LiliuxFlow</h1>
<p align="center">Apple Silicon 向けローカル LLM 配信・運用プラットフォーム</p>
<p align="center">
  <a href="https://github.com/Hiruynk/LiliuxFlow/releases"><img alt="version / v0.1.0" src="https://img.shields.io/badge/version-v0.1.0-2563eb?style=flat-square"></a>
  <a href="#requirements"><img alt="macOS / 27.0" src="https://img.shields.io/badge/macOS-27.0-4c4e75?style=flat-square&amp;logo=apple&amp;logoColor=white"></a>
  <a href="#requirements"><img alt="Apple Silicon / M5+" src="https://img.shields.io/badge/Apple_Silicon-M5%2B-e7883b?style=flat-square&amp;logo=apple&amp;logoColor=white"></a>
  <a href="LICENSE"><img alt="license / Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square"></a>
</p>

<p align="center"><a href="README.md">English</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.zh-Hans.md">简体中文</a> · <strong>日本語</strong></p>

## このプロジェクトについて

LiliuxFlow は、Apple Silicon 上でローカル LLM を配信・運用するためのネイティブプラットフォームです。Lily による推論、LiteLLM による API アクセス管理、llama-swap によるモデルのライフサイクル管理を一つのサービス構成にまとめ、多言語の管理画面と既存アプリケーション向けの API 互換レイヤーを提供します。ローカルモデルをアプリケーションに接続し、Mac 上でサービス全体を管理したい開発者を想定しています。

LiliuxFlow は、これらのコンポーネントを連携させるサービス管理、リクエストの状態に応じたライフサイクル保護、プロトコル変換を実装しています。一連の手順でネイティブ実行環境を準備し、アプリケーションごとの API キーを作成し、モデルの読み込みやキャンセルを調整して、日常の保守まで行えます。管理画面は LiteLLM、モデルのプロセス管理は llama-swap、推論は Lily が担います。

## 主な機能

- **ネイティブ環境への導入。** Rust／Metal、Python、Go、PostgreSQL をネイティブにビルドして実行します。macOS のユーザー LaunchAgent がサービスを監督し、設定、認証情報、実行データはインストールごとの専用ディレクトリに保存します。
- **アプリケーションの API アクセス管理。** LiteLLM の仮想キー、モデル権限、使用量の記録、レート制限で、アプリケーションごとのアクセスを管理できます。Chat Completions クライアントには、ローカル API のベース URL、モデル名、専用キーを設定します。
- **リクエストを考慮したモデル管理。** llama-swap が必要に応じてモデルを読み込み、一定時間アイドル状態が続くと解放します。LiliuxFlow のライフサイクルガードがリクエストの受け付けと管理操作を調整し、推論中の手動アンロードを拒否します。
- **プロトコル互換性とキャンセル。** OpenAI 互換の Chat Completions と、既存の独自チャット API を利用できます。LiliuxFlow のアダプターはプレーンテキスト応答、thinking／content SSE、呼び出し元の識別を維持し、クライアント切断に伴うキャンセルをサービス全体に伝えます。
- **4 言語のインターフェース。** ホームページと管理画面は英語、繁体字中国語、簡体字中国語、日本語に対応しています。管理画面の言語を切り替えても、フォームや進行中のストリームは維持されます。API フィールド、モデル名、ユーザー入力は元の値を保ちます。ホームページには独立したライト／ダークモード設定があります。
- **日常の運用ツール。** 同梱のネイティブツールで、インストール状態や前提条件の確認、安全なサービス停止、データのバックアップと復元を行えます。別のデータディレクトリを使えば、サービスを切り替える前に更新先の環境を準備できます。

## 画面イメージ

![LiliuxFlow のダークモードのホームページ](assets/screenshots/welcome-dark.jpg)

英語・ダークモードのホームページではサービスを紹介し、API コンソールとリファレンスへのリンクを提供しています。

![LiliuxFlow のモデル管理](assets/screenshots/model-management.jpg)

llama-swap のモデル管理画面には 64K、128K、262K のコンテキストプロファイルが並び、いずれも未ロードの状態で表示されています。

## コンポーネントの構成

```text
アプリケーション
    │
    ├── OpenAI 互換 Chat Completions ────────┐
    └── 既存チャット API → LiliuxFlow Compat ┤
                                            ▼
                                         LiteLLM ── PostgreSQL
                                            │
                               LiliuxFlow ライフサイクルガード
                                            │
                                        llama-swap
                                            │
                                           Lily

LiliuxFlow ネイティブツール → インストール／launchd／保守
```

| レイヤー | 提供元 | LiliuxFlow の統合内容 |
| --- | --- | --- |
| 推論 | [Lily](https://github.com/fabiogreter/lily-qwen3.8-flash-next) | ネイティブ導入、制御された推論設定、サービス層のパッチ |
| モデルプロセス | [llama-swap](https://github.com/mostlygeek/llama-swap) | Lily 子プロセスのラッパー、リクエストを考慮したガード、トークン指標の統合 |
| API 管理 | [LiteLLM](https://github.com/BerriAI/litellm) | モデルルーティング、呼び出し元キーの設定、多言語管理画面の統合 |
| 互換性と運用 | LiliuxFlow | 独自プロトコルの変換、レイヤー間のキャンセル、導入・保守ツール |

LiliuxFlow 独自の実装には、インストール環境の監督プログラム、モデルランナー、ライフサイクルガード、互換アダプターがあります。Lily との統合では、ログの内容のマスキング、早期のキャンセル処理、キャンセル後のセッションキャッシュの扱いも調整しています。これらは上流プロジェクトの推論・管理機能を基盤としています。各コンポーネントの役割は[アーキテクチャ](docs/ARCHITECTURE.md)を参照してください。

<a id="requirements"></a>

## 動作要件

| 項目 | 必須条件と動作確認済みの構成 |
| --- | --- |
| ホスト | macOS arm64（Apple Silicon）。Apple M5 Max、128 GiB ユニファイドメモリ搭載の Mac Studio と macOS 27.0 で動作確認 |
| ネイティブツール | uv、Rust 1.97.0、PostgreSQL 17。以下のコマンドは Python 3.12 を使用 |
| モデル | Qwen3.8-Flash-Next Lily Q4。別途ダウンロードが必要で、容量は約 98 GiB |
| 作業領域 | モデルの保存領域に加え、ビルド用に最低 64 GiB の空き容量と、実行時キャッシュ用の領域 |
| 推論設定 | 入力と出力の合計 65,536 トークン、チェックポイント既定の high thinking、QSA Split、MTP 無効 |

その他の Apple Silicon 構成は未検証です。互換性のある macOS arm64 ホストでは、動作確認済みのチップやメモリ容量と一致しなくても利用を進められます。ハードウェアの違いや RAM の空き容量不足は警告で知らせます。モデルとコンテキストキャッシュ用に十分な空きメモリを確保してください。ソースビルダーは、ネイティブプログラムと静的 UI のビルド用に固定バージョンの Go と Node を取得します。準備の詳細は[インストール](docs/INSTALLATION.md)、リソースの監視は[運用](docs/OPERATIONS.md)を参照してください。

## コンテキスト設定

| モデル | 総コンテキスト | 利用状況 |
| --- | ---: | --- |
| `qwen3.8-flash-next-lily-q4-64k` | 65,536 トークン | 有効・既定モデル |
| `qwen3.8-flash-next-lily-q4-128k` | 131,072 トークン | 有効・選択可能 |
| `qwen3.8-flash-next-lily-q4-262k` | 262,144 トークン | 有効・選択可能 |

各設定は同じ外部 Q4 チェックポイントを共有し、チェックポイント既定の high thinking、QSA Split、MTP0 を維持します。総コンテキストの上限には入力と出力の両方が含まれます。長いコンテキストを使うアプリケーションには、対象モデルへのアクセスを仮想キーに明示的に許可してください。リクエストは上限のあるキューに入り、設定の切り替えは実行中の生成とリソースの解放が完了するまで待機します。

## 同一ワークロードの性能

> [!NOTE]
> 同じ Q4 チェックポイント、MTP0、QSA Split、チェックポイント既定の high thinking、温度 0 で実測しました。キャッシュを使わない入力 4,096 トークンと出力 256 トークンを使い、各設定を交互に 3 回実行しています。値は中央値で、prefill の範囲は実行ごとの変動を示します。この共通の 4K ワークロードは、コンテキスト上限近くでの処理速度を示すものではありません。

| モデル | Prefill（tokens/s） | Prefill の範囲 | Decode（tokens/s） |
| --- | ---: | ---: | ---: |
| 64K | 1545.82 | 1379.54–1733.04 | 90.35 |
| 128K | 1347.47 | 1284.26–1806.12 | 87.72 |
| 262K | 1607.22 | 1592.39–1619.80 | 90.47 |

長いコンテキストの品質は異なるテストデータで別途確認し、入力は 128K で最大 126,464 トークン、262K で最大 257,536 トークンに達しました。設定の切り替えやコールドキャッシュの復元には読み込み時間が加わり、後続のキャッシュ利用時とはコストが異なります。API の既定設定は引き続き 64K です。

## クイックスタート

> [!IMPORTANT]
> モデルは任意で追加できます。既存の Lily 形式チェックポイントを使うか、manifest に登録されたモデルをダウンロードするか、管理サービスを先にセットアップして後からモデルを追加できます。このリポジトリにモデルの重みは含まれません。

| 追加できるモデル | 形式 | ダウンロード容量 | 配布元とライセンス |
| --- | --- | --- | --- |
| Qwen3.8-Flash-Next | Lily Q4 | 約 98.3 GiB | [上流モデル](https://huggingface.co/fabiogreter/Qwen3.8-Flash-Next-lily-q4) · [Qwen Community License 1.0](docs/productization/MODEL_LICENSE.txt) |

### ソースを取得する

リポジトリを clone するか、`SOURCE_COMMIT.json` を含む公式 LiliuxFlow ソースリリースの配布ファイルを展開してください。

```sh
git clone https://github.com/Hiruynk/LiliuxFlow.git
cd LiliuxFlow
```

GitHub が自動生成する **Source code (zip/tar.gz)** にはこのソース記録が含まれないため、そのままでは標準のビルド手順を利用できません。ソースとセットアップの詳細は[インストール](docs/INSTALLATION.md)を参照してください。

既存モデルを使う場合は、完全なチェックポイントのディレクトリを `MODEL_DIR` に指定してください。setup はその場所を直接使用します。

リポジトリのルートで実行します。

```sh
export MODEL_DIR="$HOME/Models/Qwen3.8-Flash-Next-lily-q4"

# 現在のシェルで使う補助関数です。
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
> この手順で実行環境を準備し、ダウンロードしたモデルファイルを検証し、インストール用データベースを初期化して、起動条件を確認したうえでローカルサービスを開始します。モデルは必要になった時点で読み込まれます。サービスが正常でも、アイドル時にはモデルがメモリ上にない場合があるため、最初のモデルリクエストには読み込み時間が加わります。

`lf models list` と `lf models info qwen3.8-flash-next-lily-q4` は、オフラインでモデル一覧と詳細を表示します。ダウンロードする場合は、明示的に次のインストール手順を実行します。

```sh
mkdir -p "$(dirname "$MODEL_DIR")"
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --dry-run
lf models install qwen3.8-flash-next-lily-q4 --output "$MODEL_DIR" --execute
```

`--dry-run` は Hugging Face に接続してダウンロードのメタデータを取得し、`--execute` はモデルファイルをダウンロードします。管理サービスを先にセットアップする場合は、上記のモデルディレクトリを指定する setup を `lf setup --without-model` に置き換え、`lf verify-model` を省略してください。モデルを追加する前に管理サービスを利用できます。推論の提供を始めるときに、モデルを追加して検証してください。後からモデルを追加する手順は[インストール](docs/INSTALLATION.md)を参照してください。

| 用途 | 既定の接続先 |
| --- | --- |
| API 管理 | <http://127.0.0.1:4000/ui/> |
| モデル管理 | <http://127.0.0.1:8080/ui/> |
| Chat Completions API base | `http://127.0.0.1:4000/v1` |

### 管理画面を開く

既定のデータディレクトリは `~/Library/Application Support/LiliuxFlow` です。その中の `secrets/bootstrap.json` をローカルで開くと管理画面のログイン情報、`secrets/caller.json` を開くとアプリケーションキーと接続設定を確認できます。同じ設定で setup を再実行しても、既存の設定や認証情報は保持されます。全サービスはループバックで待ち受けます。既定のポートが使用中の場合は、setup 時に別のポートを選んでください。

<a id="connect-an-application"></a>

## アプリケーションを接続する

同梱クライアントは、インストール環境の呼び出し元設定を読み取り、Chat Completions の応答をストリーム表示します。

```sh
uv run --no-project --python 3.12 python scripts/distribution/client_example.py --api openai --stream
```

別のクライアントには、API base **`http://127.0.0.1:4000/v1`**、モデル **`qwen3.8-flash-next-lily-q4-64k`**、そのアプリケーションに割り当てた LiteLLM 仮想キーを設定します。モデル権限や制限は API 管理画面で管理できます。既定の出力予算は thinking を含む 4,096 トークンで、入力と出力の合計 65,536 トークンの範囲内に収める必要があります。

この構成は Chat Completions を提供します。既存インターフェースは独自 SSE またはプレーンテキストを使用するため、クライアントの改修時にはその通信形式に従ってください。JSON／JSON Schema 応答形式、音声／動画、リクエストごとの `keep_alive` はサポートしていません。既存 API の例、対応オプション、キャンセル動作は [API](docs/API.md)を参照してください。

## ドキュメントと開発

- [インストール](docs/INSTALLATION.md)：前提条件、モデルの準備、独立したインストール環境。
- [API](docs/API.md)：クライアント設定、既存プロトコル、対応するリクエストオプション。
- [運用](docs/OPERATIONS.md)：状態確認、ログ、バックアップ、復元、更新。
- [アーキテクチャ](docs/ARCHITECTURE.md)：上流コンポーネントと LiliuxFlow の統合。
- [Cloudflare Edge（任意）](docs/cloudflare-edge.md)：Worker の静的アセットと、アクセス範囲を限定した VPC Service 経由でのプライベートバックエンドへの接続。
- [第三者ライセンスの通知](THIRD_PARTY_NOTICES.md)：コンポーネントのライセンスと帰属。

日常の確認には `lf status` または `lf doctor`、サービスの停止には `lf stop` を使用します。変更を提案する際は、呼び出し元の識別、キャンセル動作、通信形式の互換性、上流へのクレジットを維持してください。ネイティブコンポーネントの変更は、同梱のソースマニフェストとビルドレシピから再現できるようにします。

## ライセンスと謝辞

LiliuxFlow 独自のコード、ドキュメント、プロジェクト所有のアセットは [Apache License 2.0](LICENSE) で提供します。第三者コンポーネントにはそれぞれのライセンスが適用されます。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) と [NOTICE](NOTICE) を参照してください。モデルの重みは別途取得する必要があり、その[モデルライセンス](docs/productization/MODEL_LICENSE.txt)に従います。

LiliuxFlow は Lily、LiteLLM、llama-swap を基盤としており、配布物には各プロジェクトの著作者表示とライセンス通知を保持しています。
