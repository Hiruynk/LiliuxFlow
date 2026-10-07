    (() => {
      'use strict';
      const catalogs = {
  "en": {
    "title": "LiliuxFlow",
    "skip": "Skip to content",
    "navLabel": "Main navigation",
    "private": "Private",
    "githubLabel": "GitHub repository, opens in a new tab",
    "language": "Language",
    "navDashboard": "Open Console ↗",
    "description": "A local LLM platform for Apple Silicon. Manage API access, model lifecycle and native inference in one place.",
    "dashboard": "Open Console",
    "explore": "Explore the API",
    "copy": "Copy",
    "copyLabel": "Copy code example",
    "codeLabel": "Code example",
    "sdk": "Connect with the OpenAI SDK",
    "caption": "API endpoint example. Use your configured base URL and virtual API key.",
    "platformKicker": "Designed for local operations",
    "platformTitle": "The essentials, integrated.",
    "platformIntro": "Manage models, API access and development workflows in one workspace.",
    "modelsTitle": "Model lifecycle, managed.",
    "modelsDescription": "Manage model lifecycle and inference service settings in one console.",
    "keysTitle": "API access, under control.",
    "keysDescription": "Give each application its own virtual API key. Manage model permissions, budgets and usage.",
    "workflowTitle": "Fits your workflow.",
    "workflowDescription": "Use Chat Completions, model listings and your existing OpenAI SDK.",
    "apiKicker": "An OpenAI-compatible API",
    "apiTitle": "Connect. Start building.",
    "apiIntro": "Set the base URL, add your virtual API key, then select a model.",
    "apiDocs": "API reference",
    "apiCaveat": "Reasoning and tool calling depend on the model and runtime configuration. Validate tool arguments returned by the model before execution. See the API reference for limits.",
    "attribution": "Management interface powered by LiteLLM; model management by llama-swap; inference by Lily.",
    "backTop": "Back to top",
    "copied": "Code example copied.",
    "copyFailed": "Select and copy the example manually.",
    "backendOffline": "LiliuxFlow backend is currently offline.",
    "localeChanged": "Language changed to English.",
    "topologyHeading": "One workspace. A complete serving stack.",
    "illustration": "Architecture illustration",
    "controlPlane": "Control plane",
    "client": "Client",
    "clientRole": "SDK · application",
    "gatewayRole": "API keys · routing · usage",
    "managerRole": "Model lifecycle",
    "runtimeRole": "Native inference",
    "metalRole": "Apple GPU",
    "returnLabel": "SSE streaming response · reasoning / content",
    "topologyCaption": "Illustrative request and response flow. No live traffic or performance data.",
    "architectureKicker": "From request to inference",
    "architectureTitle": "Every layer, with a purpose.",
    "architectureIntro": "An OpenAI-compatible API, backed by native inference.",
    "pipelineGateway": "Control API access, enforce model permissions and track usage.",
    "pipelineManager": "Route requests to models and manage the inference service lifecycle.",
    "pipelineRuntime": "Run inference natively with Rust and Metal.",
    "pipelineHardware": "Run inference using the GPU and unified memory.",
    "totalContext": "total context",
    "callerKey": "Virtual API key",
    "streamTitle": "Responses, streamed.",
    "streamDescription": "Stream reasoning and content, with support for request cancellation.",
    "illustrativeTrace": "Illustrative trace",
    "memoryTitle": "Unified memory, shared.",
    "memoryDescription": "Run the model in Apple Silicon unified memory, with a total context limit of {context} tokens and resource safeguards.",
    "unifiedMemory": "Unified memory",
    "nativeTitle": "Native stack. Local execution.",
    "nativeDescription": "Rust, Metal and native services. Manage models and services through the existing consoles.",
    "nativeStack": "Native stack",
    "exampleLanguage": "Example language",
    "motionPause": "Pause animation",
    "motionResume": "Resume animation",
    "navOverview": "Overview",
    "navArchitecture": "Architecture",
    "heroTagline": "Your models. Your silicon. Your infrastructure.",
    "motionReduced": "Reduced motion",
    "themeDark": "Switch to dark mode",
    "themeLight": "Switch to light mode"
  },
  "zh-Hant": {
    "title": "LiliuxFlow",
    "skip": "跳至主要內容",
    "navLabel": "主要導覽",
    "private": "私有",
    "githubLabel": "GitHub 儲存庫，在新分頁開啟",
    "language": "語言",
    "navDashboard": "管理介面 ↗",
    "description": "專為 Apple Silicon 而設的本地 LLM 平台，集中管理 API 存取、模型生命週期與原生推理。",
    "dashboard": "開啟管理介面",
    "explore": "探索 API",
    "copy": "複製",
    "copyLabel": "複製程式碼範例",
    "codeLabel": "程式碼範例",
    "sdk": "可用 OpenAI SDK 連接",
    "caption": "API 端點範例。請使用已設定的 base URL 與虛擬 API 金鑰。",
    "platformKicker": "專為本地維運而設",
    "platformTitle": "所需功能，一處整合。",
    "platformIntro": "在同一工作空間整合模型管理、API 存取與開發流程。",
    "modelsTitle": "模型生命週期，集中管理。",
    "modelsDescription": "集中管理模型生命週期與推理服務設定。",
    "keysTitle": "存取權限，由你決定。",
    "keysDescription": "為每個應用程式配置獨立的虛擬 API 金鑰，管理模型權限、預算與用量。",
    "workflowTitle": "融入你的開發流程。",
    "workflowDescription": "使用 Chat Completions、模型列表與現有的 OpenAI SDK。",
    "apiKicker": "OpenAI 相容 API",
    "apiTitle": "連接 API，開始開發。",
    "apiIntro": "設定 base URL、填入虛擬 API 金鑰，再選擇模型。",
    "apiDocs": "API 參考文件",
    "apiCaveat": "推理內容與工具呼叫的支援取決於模型及執行配置。使用工具呼叫前，請驗證模型傳回的工具引數；功能限制請參閱 API 參考文件。",
    "attribution": "管理介面由 LiteLLM 提供；模型管理由 llama-swap 提供；推理由 Lily 執行。",
    "backTop": "回到頂端",
    "copied": "已複製程式碼範例。",
    "copyFailed": "請選取範例並手動複製。",
    "backendOffline": "LiliuxFlow 後端目前離線。",
    "localeChanged": "已切換為繁體中文。",
    "topologyHeading": "一個工作空間，整合完整服務流程。",
    "illustration": "架構示意",
    "controlPlane": "控制平面",
    "client": "用戶端",
    "clientRole": "SDK · 應用程式",
    "gatewayRole": "API 金鑰 · 路由 · 用量",
    "managerRole": "模型生命週期",
    "runtimeRole": "原生推理",
    "metalRole": "Apple GPU",
    "returnLabel": "SSE 串流回應 · 推理 / 內容",
    "topologyCaption": "請求與回應流程示意，並非即時流量或效能數據。",
    "architectureKicker": "從請求到推理",
    "architectureTitle": "每一層，各司其職。",
    "architectureIntro": "提供 OpenAI 相容 API，底層採用原生推理。",
    "pipelineGateway": "負責 API 存取控制、模型權限與用量追蹤。",
    "pipelineManager": "負責模型路由與推理服務生命週期管理。",
    "pipelineRuntime": "以 Rust 與 Metal 提供原生推理。",
    "pipelineHardware": "利用 GPU 與統一記憶體架構執行推理。",
    "totalContext": "總上下文",
    "callerKey": "虛擬 API 金鑰",
    "streamTitle": "回應，即時串流。",
    "streamDescription": "串流傳送推理與回應內容，並支援取消請求。",
    "illustrativeTrace": "流程示意",
    "memoryTitle": "統一記憶體架構。",
    "memoryDescription": "在 Apple Silicon 統一記憶體中執行模型，總上下文上限為 {context} tokens，並設有資源保護機制。",
    "unifiedMemory": "統一記憶體",
    "nativeTitle": "完整技術堆疊，本機執行。",
    "nativeDescription": "採用 Rust、Metal 與原生服務，透過管理介面管理模型與服務。",
    "nativeStack": "原生技術堆疊",
    "exampleLanguage": "範例語言",
    "motionPause": "暫停動畫",
    "motionResume": "播放動畫",
    "navOverview": "概覽",
    "navArchitecture": "架構",
    "heroTagline": "模型、晶片、基礎設施，由你掌握。",
    "motionReduced": "已減少動態效果",
    "themeDark": "切換至深色模式",
    "themeLight": "切換至淺色模式"
  },
  "zh-Hans": {
    "title": "LiliuxFlow",
    "skip": "跳转到主要内容",
    "navLabel": "主导航",
    "private": "私有",
    "githubLabel": "GitHub 仓库，在新标签页中打开",
    "language": "语言",
    "navDashboard": "管理界面 ↗",
    "description": "面向 Apple Silicon 的本地 LLM 平台，集中管理 API 访问、模型生命周期和原生推理。",
    "dashboard": "打开管理界面",
    "explore": "探索 API",
    "copy": "复制",
    "copyLabel": "复制代码示例",
    "codeLabel": "代码示例",
    "sdk": "可用 OpenAI SDK 连接",
    "caption": "API 端点示例。请使用已配置的 base URL 和虚拟 API 密钥。",
    "platformKicker": "专为本地运维而设计",
    "platformTitle": "核心功能，一处集成。",
    "platformIntro": "在同一个工作空间集中管理模型、API 访问和开发流程。",
    "modelsTitle": "模型生命周期，集中管理。",
    "modelsDescription": "集中管理模型生命周期和推理服务配置。",
    "keysTitle": "API 访问，由你决定。",
    "keysDescription": "为每个应用分配独立的虚拟 API 密钥，管理模型权限、预算和用量。",
    "workflowTitle": "融入你的开发流程。",
    "workflowDescription": "使用 Chat Completions、模型列表和现有的 OpenAI SDK。",
    "apiKicker": "兼容 OpenAI 的 API",
    "apiTitle": "连接 API，开始开发。",
    "apiIntro": "配置 base URL，填入虚拟 API 密钥，然后选择模型。",
    "apiDocs": "API 参考文档",
    "apiCaveat": "推理内容和工具调用的支持取决于模型与运行配置。使用工具调用前，请校验模型返回的工具参数；功能限制请参阅 API 参考文档。",
    "attribution": "管理界面由 LiteLLM 提供；模型管理由 llama-swap 提供；推理由 Lily 执行。",
    "backTop": "返回顶部",
    "copied": "已复制代码示例。",
    "copyFailed": "请选中示例并手动复制。",
    "backendOffline": "LiliuxFlow 后端当前离线。",
    "localeChanged": "已切换为简体中文。",
    "topologyHeading": "一个工作空间，贯通完整服务流程。",
    "illustration": "架构示意",
    "controlPlane": "控制平面",
    "client": "客户端",
    "clientRole": "SDK · 应用",
    "gatewayRole": "API 密钥 · 路由 · 用量",
    "managerRole": "模型生命周期",
    "runtimeRole": "原生推理",
    "metalRole": "Apple GPU",
    "returnLabel": "SSE 流式响应 · 推理 / 内容",
    "topologyCaption": "请求与响应流程示意，并非实时流量或性能数据。",
    "architectureKicker": "从请求到推理",
    "architectureTitle": "每一层，各司其职。",
    "architectureIntro": "提供兼容 OpenAI 的 API，底层采用原生推理。",
    "pipelineGateway": "负责 API 访问控制、模型权限管理和用量追踪。",
    "pipelineManager": "负责模型路由及推理服务的生命周期管理。",
    "pipelineRuntime": "通过 Rust 和 Metal 执行原生推理。",
    "pipelineHardware": "通过 GPU 和统一内存架构执行推理。",
    "totalContext": "总上下文",
    "callerKey": "虚拟 API 密钥",
    "streamTitle": "流式响应，逐步呈现。",
    "streamDescription": "以流式方式传输推理和响应内容，并支持取消请求。",
    "illustrativeTrace": "流程示意",
    "memoryTitle": "统一内存架构。",
    "memoryDescription": "在 Apple Silicon 统一内存中运行模型，总上下文上限为 {context} tokens，并设有资源保护措施。",
    "unifiedMemory": "统一内存",
    "nativeTitle": "原生技术栈，本地运行。",
    "nativeDescription": "采用 Rust、Metal 和原生服务，通过管理界面管理模型与服务。",
    "nativeStack": "原生技术栈",
    "exampleLanguage": "示例语言",
    "motionPause": "暂停动画",
    "motionResume": "播放动画",
    "navOverview": "概览",
    "navArchitecture": "架构",
    "heroTagline": "模型、芯片、基础设施，由你掌握。",
    "motionReduced": "已减少动态效果",
    "themeDark": "切换至深色模式",
    "themeLight": "切换至浅色模式"
  },
  "ja": {
    "title": "LiliuxFlow",
    "skip": "メインコンテンツへ移動",
    "navLabel": "メインナビゲーション",
    "private": "非公開",
    "githubLabel": "GitHub リポジトリを新しいタブで開く",
    "language": "言語",
    "navDashboard": "管理画面 ↗",
    "description": "Apple Silicon 向けのローカル LLM プラットフォーム。API アクセス、モデルライフサイクル、ネイティブ推論をまとめて管理。",
    "dashboard": "管理画面を開く",
    "explore": "API を見る",
    "copy": "コピー",
    "copyLabel": "コード例をコピー",
    "codeLabel": "コード例",
    "sdk": "OpenAI SDK で接続",
    "caption": "API エンドポイントの例です。設定済みの base URL と仮想 API キーを使用してください。",
    "platformKicker": "ローカル運用のために",
    "platformTitle": "必要な機能を、ひとつに。",
    "platformIntro": "モデル管理、API アクセス、開発フローを一つの環境に。",
    "modelsTitle": "モデルを、一元管理。",
    "modelsDescription": "モデルライフサイクルと推論サービスの設定をまとめて管理。",
    "keysTitle": "API アクセスを、適切に。",
    "keysDescription": "アプリケーションごとに仮想 API キーを発行。モデル権限、予算、使用量を管理。",
    "workflowTitle": "いつもの開発フローで。",
    "workflowDescription": "Chat Completions、モデル一覧、既存の OpenAI SDK を利用できます。",
    "apiKicker": "OpenAI 互換 API",
    "apiTitle": "API 接続から、開発へ。",
    "apiIntro": "base URL と仮想 API キーを設定し、モデルを選択します。",
    "apiDocs": "API リファレンス",
    "apiCaveat": "推論内容とツール呼び出しの対応状況は、モデルと実行設定によります。実行前にモデルが返すツール引数を検証し、制限は API リファレンスで確認してください。",
    "attribution": "管理画面は LiteLLM、モデル管理は llama-swap、推論は Lily によって提供されます。",
    "backTop": "ページ上部へ",
    "copied": "コード例をコピーしました。",
    "copyFailed": "コード例を選択して手動でコピーしてください。",
    "backendOffline": "LiliuxFlow バックエンドは現在オフラインです。",
    "localeChanged": "日本語に切り替えました。",
    "topologyHeading": "ひとつの環境で、リクエストから推論まで。",
    "illustration": "構成のイメージ",
    "controlPlane": "コントロールプレーン",
    "client": "クライアント",
    "clientRole": "SDK · アプリ",
    "gatewayRole": "キー · ルーティング · 使用量",
    "managerRole": "モデルライフサイクル",
    "runtimeRole": "ネイティブ推論",
    "metalRole": "Apple GPU",
    "returnLabel": "SSE ストリーミング · 推論 / 内容",
    "topologyCaption": "リクエストとレスポンスの流れを示したイメージです。実際のトラフィックや性能データではありません。",
    "architectureKicker": "リクエストから推論へ",
    "architectureTitle": "それぞれの層に、役割を。",
    "architectureIntro": "OpenAI 互換 API を入り口に、ネイティブ推論へ。",
    "pipelineGateway": "API アクセスを制御し、モデル権限を適用して使用量を記録。",
    "pipelineManager": "モデルへのルーティングと推論サービスのライフサイクルを管理。",
    "pipelineRuntime": "Rust と Metal でネイティブ推論を実行。",
    "pipelineHardware": "GPU とユニファイドメモリで推論を実行。",
    "totalContext": "総コンテキスト",
    "callerKey": "仮想 API キー",
    "streamTitle": "応答をストリーミングで。",
    "streamDescription": "推論内容と応答をストリーミングで返し、リクエストのキャンセルにも対応。",
    "illustrativeTrace": "処理のイメージ",
    "memoryTitle": "ユニファイドメモリ。",
    "memoryDescription": "Apple Silicon のユニファイドメモリでモデルを実行します。総コンテキスト上限は {context} トークンで、リソース保護も備えています。",
    "unifiedMemory": "ユニファイドメモリ",
    "nativeTitle": "すべてを、ローカルで。",
    "nativeDescription": "Rust、Metal、ネイティブサービスで構成。管理画面からモデルとサービスを操作できます。",
    "nativeStack": "ネイティブ構成",
    "exampleLanguage": "コード例の言語",
    "motionPause": "アニメーションを停止",
    "motionResume": "アニメーションを再開",
    "navOverview": "概要",
    "navArchitecture": "構成",
    "heroTagline": "モデルも、シリコンも、インフラも、あなたの手に。",
    "motionReduced": "動きを抑える設定",
    "themeDark": "ダークモードに切り替え",
    "themeLight": "ライトモードに切り替え"
  }
};
      const language = document.getElementById('language');
      const picker = document.getElementById('language-picker');
      const languageMenu = document.getElementById('language-menu');
      const languageCurrent = document.getElementById('language-current');
      const languageOptions = Array.from(languageMenu.querySelectorAll('[data-locale]'));
      const autonyms = {en:'English', 'zh-Hant':'繁體中文', 'zh-Hans':'简体中文', ja:'日本語'};
      const toast = document.getElementById('toast');
      let current = 'en';
      let currentTheme = 'dark';
      const themeControl = document.getElementById('theme-toggle');
      const colorScheme = document.getElementById('welcome-color-scheme');
      let toastTimer;
      const copyControl = document.getElementById('copy-code');
      let copySucceeded = false;
      let copyResetTimer;
      let copyAttempt = 0;
      let languageClose = 0;
      let selectedCode = 'python';
      let userPaused = false;
      const motionControl = document.getElementById('motion-toggle');
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
      const regions = Array.from(document.querySelectorAll('.animated-region'));
      const regionVisibility = new Map(regions.map(region => [region, true]));
      const contextModels = Object.freeze({
        'qwen3.8-flash-next-lily-q4-64k': 65536,
        'qwen3.8-flash-next-lily-q4-128k': 131072,
        'qwen3.8-flash-next-lily-q4-262k': 262144,
      });
      const configuredModel = document.body.dataset.contextModel;
      const configuredContext = Number(document.body.dataset.contextTokens);
      const welcomeProfile = Object.freeze(Object.prototype.hasOwnProperty.call(contextModels, configuredModel) && contextModels[configuredModel] === configuredContext
        ? {model: configuredModel, context: configuredContext}
        : {model: 'qwen3.8-flash-next-lily-q4-64k', context: 65536});
      const contextLabel = String(welcomeProfile.context).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      document.querySelectorAll('[data-profile-model]').forEach(element => { element.textContent = welcomeProfile.model; });
      document.querySelectorAll('[data-profile-context]').forEach(element => { element.textContent = contextLabel; });
      const valid = value => Object.prototype.hasOwnProperty.call(catalogs, value);
      const preference = () => {
        const entries = document.cookie.split(';').map(value => value.trim()).filter(value => value.startsWith('liliuxflow_locale='));
        const value = entries.length === 1 ? entries[0].slice('liliuxflow_locale='.length) : null;
        return valid(value) ? value : null;
      };
      function apply(locale) {
        if (!valid(locale)) return;
        current = locale;
        const messages = catalogs[locale];
        document.documentElement.lang = locale;
        document.title = messages.title;
        languageCurrent.textContent = autonyms[locale];
        languageOptions.forEach(option => { option.setAttribute('aria-selected', String(option.dataset.locale === locale)); });
        document.querySelectorAll('[data-i18n]').forEach(element => { element.textContent = messages[element.dataset.i18n].replace('{context}', contextLabel); });
        document.querySelectorAll('[data-i18n-aria]').forEach(element => { element.setAttribute('aria-label', messages[element.dataset.i18nAria]); });
        if (copySucceeded) copyControl.setAttribute('aria-label', messages.copied);
        language.setAttribute('aria-label', `${messages.language}: ${autonyms[locale]}`);
        updateMotionControl();
        updateThemeControl();
        if (window.WelcomePolish) window.WelcomePolish.setTagline(locale, messages.heroTagline);
      }
      function announce(message) {
        clearTimeout(toastTimer);
        toast.textContent = message;
        toast.classList.add('visible');
        toastTimer = setTimeout(() => { toast.classList.remove('visible'); }, 3200);
      }
      async function withBackendGuard(action, unavailable = () => {}) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 3000);
        try {
          const response = await fetch('/_liliuxflow/backend/ready', {
            method: 'GET', cache: 'no-store', signal: controller.signal
          });
          if (response.status !== 204) throw new Error('Backend unavailable');
        } catch (_) {
          unavailable();
          announce(catalogs[current].backendOffline);
          return;
        } finally {
          clearTimeout(timeout);
        }
        action();
      }
      const pendingBackendLinks = new WeakSet();
      // Only the edge builder opts in. The same source remains a normal local
      // Welcome page, whose backend does not own the Worker readiness endpoint.
      if (document.body.dataset.edgeBackendGuard === 'true') document.querySelectorAll('a[href="/ui/"], a[href="/ui"], a[href="/api-docs"]').forEach(link => {
        const openBackend = async event => {
          if (event.defaultPrevented || event.type === 'auxclick' && event.button !== 1
              || event.type === 'click' && event.button !== 0) return;
          event.preventDefault();
          if (pendingBackendLinks.has(link)) return;
          pendingBackendLinks.add(link);
          const href = link.getAttribute('href');
          const newTab = event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1 || link.getAttribute('target') === '_blank';
          // Reserve the tab inside the user gesture so Safari can open it after
          // the asynchronous probe. The backend is never loaded before 204.
          const reservedTab = newTab ? window.open('about:blank', '_blank') : null;
          if (reservedTab) reservedTab.opener = null;
          try {
            await withBackendGuard(() => {
              if (newTab) {
                if (reservedTab && !reservedTab.closed) reservedTab.location.replace(href);
                else if (!reservedTab) window.open(href, '_blank', 'noopener,noreferrer');
              } else window.location.assign(href);
            }, () => { if (reservedTab && !reservedTab.closed) reservedTab.close(); });
          } finally {
            pendingBackendLinks.delete(link);
          }
        };
        link.addEventListener('click', openBackend);
        link.addEventListener('auxclick', openBackend);
      });
      function closeLanguages(returnFocus = false) {
        if (languageMenu.hidden || languageMenu.classList.contains('language-closing')) {
          if (returnFocus) language.focus({preventScroll:true});
          return;
        }
        const closing = ++languageClose;
        language.setAttribute('aria-expanded', 'false');
        languageOptions.forEach(option => { option.tabIndex = -1; });
        languageMenu.classList.add('language-closing');
        languageMenu.inert = true;
        if (returnFocus) language.focus({preventScroll:true});
        const finish = () => {
          if (closing !== languageClose) return;
          languageMenu.hidden = true;
          languageMenu.classList.remove('language-closing');
          languageMenu.inert = false;
        };
        if (window.WelcomePolish) window.WelcomePolish.closeLanguageMenu(languageMenu, finish);
        else finish();
      }
      function focusOption(index) {
        languageOptions.forEach((option, position) => { option.tabIndex = position === index ? 0 : -1; });
        languageOptions[index].focus({preventScroll:true});
      }
      function openLanguages(index = languageOptions.findIndex(option => option.dataset.locale === current)) {
        ++languageClose;
        languageMenu.classList.remove('language-closing');
        languageMenu.inert = false;
        languageMenu.hidden = false;
        if (window.WelcomePolish) window.WelcomePolish.animateLanguageMenu(languageMenu);
        language.setAttribute('aria-expanded', 'true');
        focusOption(index);
      }
      function selectLanguage(locale) {
        if (!valid(locale)) return;
        document.cookie = `liliuxflow_locale=${locale}; Path=/; Max-Age=31536000; SameSite=Lax`;
        apply(locale);
        closeLanguages(true);
        announce(catalogs[current].localeChanged);
      }
      language.addEventListener('click', () => {
        if (languageMenu.hidden || languageMenu.classList.contains('language-closing')) openLanguages();
        else closeLanguages(true);
      });
      language.addEventListener('keydown', event => {
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const index = event.key === 'Home' ? 0 : event.key === 'End' ? languageOptions.length - 1 : languageOptions.findIndex(option => option.dataset.locale === current);
          openLanguages(index);
        } else if (event.key === 'Escape' && !languageMenu.hidden) {
          event.preventDefault();
          closeLanguages(true);
        }
      });
      languageOptions.forEach(option => { option.addEventListener('click', () => selectLanguage(option.dataset.locale)); });
      languageMenu.addEventListener('keydown', event => {
        const index = languageOptions.indexOf(document.activeElement);
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? languageOptions.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + languageOptions.length) % languageOptions.length;
          focusOption(next);
        } else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          if (index >= 0) selectLanguage(languageOptions[index].dataset.locale);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          closeLanguages(true);
        } else if (event.key === 'Tab') {
          closeLanguages(true);
        }
      });
      document.addEventListener('pointerdown', event => { if (!picker.contains(event.target)) closeLanguages(); });
      picker.addEventListener('focusout', event => { if (!picker.contains(event.relatedTarget)) closeLanguages(); });
      function themePreference() {
        const entries = document.cookie.split(';').map(value => value.trim()).filter(value => value.startsWith('liliuxflow_welcome_theme='));
        const value = entries.length === 1 ? entries[0].slice('liliuxflow_welcome_theme='.length) : null;
        return value === 'light' || value === 'dark' ? value : null;
      }
      function updateThemeControl() {
        const label = catalogs[current][currentTheme === 'dark' ? 'themeLight' : 'themeDark'];
        themeControl.setAttribute('aria-pressed', String(currentTheme === 'dark'));
        themeControl.setAttribute('aria-label', label);
        themeControl.setAttribute('title', label);
      }
      function applyTheme(theme, animate = false) {
        if (theme !== 'light' && theme !== 'dark') return;
        const change = () => {
          currentTheme = theme;
          document.documentElement.setAttribute('data-theme', theme);
          colorScheme.setAttribute('content', theme);
          updateThemeControl();
        };
        if (animate && window.WelcomePolish) window.WelcomePolish.transitionTheme(change);
        else change();
      }
      themeControl.addEventListener('click', () => {
        const next = currentTheme === 'light' ? 'dark' : 'light';
        document.cookie = `liliuxflow_welcome_theme=${next}; Path=/; Max-Age=31536000; SameSite=Lax`;
        applyTheme(next, true);
      });
      function syncPreference() {
        const saved = preference();
        if (saved && saved !== current) apply(saved);
        const savedTheme = themePreference();
        if (savedTheme && savedTheme !== currentTheme) applyTheme(savedTheme, true);
      }
      window.addEventListener('focus', syncPreference);
      document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') syncPreference(); });
      function setCopyFeedback(succeeded) {
        clearTimeout(copyResetTimer);
        copySucceeded = succeeded;
        copyControl.classList.toggle('copy-succeeded', succeeded);
        copyControl.setAttribute('aria-label', catalogs[current][succeeded ? 'copied' : 'copyLabel']);
        if (succeeded) copyResetTimer = setTimeout(() => setCopyFeedback(false), 1600);
      }
      copyControl.addEventListener('click', async () => {
        const attempt = ++copyAttempt;
        const example = document.getElementById('example-code');
        try {
          if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(example.textContent);
          } else {
            const field = document.createElement('textarea');
            field.value = example.textContent;
            field.setAttribute('readonly', '');
            field.className = 'clipboard-field';
            document.body.appendChild(field);
            field.select();
            let copied = false;
            try { copied = document.execCommand('copy'); }
            finally { field.remove(); }
            document.getElementById('copy-code').focus({preventScroll:true});
            if (!copied) throw new Error('Copy unavailable');
          }
          if (attempt !== copyAttempt) return;
          setCopyFeedback(true);
          announce(catalogs[current].copied);
        } catch {
          if (attempt !== copyAttempt) return;
          setCopyFeedback(false);
          const range = document.createRange();
          range.selectNodeContents(example);
          const selection = window.getSelection();
          if (selection) { selection.removeAllRanges(); selection.addRange(range); }
          example.parentElement.focus({preventScroll:true});
          announce(catalogs[current].copyFailed);
        }
      });
      // An installer may supply an explicit HTTP(S) API base. Otherwise use
      // this page's origin; file previews use the native loopback endpoint.
      function exampleApiBase(configured, origin) {
        function normalize(value) {
          if (typeof value !== 'string' || !/^https?:\/\//i.test(value.trim()) || /[\u0000-\u0020\u007f"'`\\$<>]/.test(value.trim())) return null;
          try {
            const url = new URL(value.trim());
            if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
            url.pathname = url.pathname.replace(/\/+$/, '').replace(/(?:\/v1)+$/, '') + '/v1';
            return url.href.replace(/\/+$/, '');
          } catch { return null; }
        }
        return normalize(configured) || normalize(origin) || 'http://127.0.0.1:4000/v1';
      }
      const apiBase = exampleApiBase(document.body.dataset.apiBase, window.location.origin);
      const examples = {
        python: `from openai import OpenAI

client = OpenAI(
    base_url="${apiBase}",
    api_key="YOUR_VIRTUAL_KEY",
)

response = client.chat.completions.create(
    model="${welcomeProfile.model}",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)`,
        curl: `curl "${apiBase}/chat/completions" \\
  -H "Authorization: Bearer YOUR_VIRTUAL_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${welcomeProfile.model}",
    "messages": [{"role": "user", "content": "Hello"}]
  }'`,
        javascript: `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "${apiBase}",
  apiKey: "YOUR_VIRTUAL_KEY",
});

const response = await client.chat.completions.create({
  model: "${welcomeProfile.model}",
  messages: [{ role: "user", content: "Hello" }],
});
console.log(response.choices[0].message.content);`
      };
      const codeTabs = Array.from(document.querySelectorAll('[data-code]'));
      function showCode(name, focus = false) {
        if (!Object.prototype.hasOwnProperty.call(examples, name)) return;
        selectedCode = name;
        const example = document.getElementById('example-code');
        example.replaceChildren();
        const tokenPattern = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\b(from|import|const|await|print)\b/g;
        let last = 0;
        for (const token of examples[name].matchAll(tokenPattern)) {
          example.appendChild(document.createTextNode(examples[name].slice(last, token.index)));
          const span = document.createElement('span');
          span.className = token[1] ? 'code-string' : 'code-keyword';
          span.textContent = token[0];
          example.appendChild(span);
          last = token.index + token[0].length;
        }
        example.appendChild(document.createTextNode(examples[name].slice(last)));
        codeTabs.forEach(tab => {
          const selected = tab.dataset.code === name;
          tab.setAttribute('aria-selected', String(selected));
          tab.tabIndex = selected ? 0 : -1;
          if (selected && focus) tab.focus({preventScroll:true});
        });
        document.getElementById('code-panel').setAttribute('aria-labelledby', `tab-${name}`);
      }
      codeTabs.forEach(tab => {
        tab.addEventListener('click', () => showCode(tab.dataset.code));
        tab.addEventListener('keydown', event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const position = codeTabs.indexOf(tab);
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? codeTabs.length - 1 : (position + (event.key === 'ArrowRight' ? 1 : -1) + codeTabs.length) % codeTabs.length;
          showCode(codeTabs[next].dataset.code, true);
        });
      });
      function updateMotionControl() {
        const paused = userPaused || reducedMotion.matches;
        document.body.classList.toggle('motion-disabled', paused);
        if (window.WelcomePolish) window.WelcomePolish.setMotionDisabled(paused);
        motionControl.setAttribute('aria-pressed', String(paused));
        const label = catalogs[current][reducedMotion.matches ? 'motionReduced' : userPaused ? 'motionResume' : 'motionPause'];
        motionControl.setAttribute('aria-label', label);
        motionControl.setAttribute('title', label);
        motionControl.disabled = reducedMotion.matches;
      }
      function updateMotion() {
        regions.forEach(region => region.classList.toggle('motion-paused', document.hidden || !regionVisibility.get(region)));
        updateMotionControl();
      }
      motionControl.addEventListener('click', () => { userPaused = !userPaused; updateMotion(); });
      if (typeof IntersectionObserver === 'function') {
        const observer = new IntersectionObserver(entries => {
          entries.forEach(entry => regionVisibility.set(entry.target, entry.isIntersecting));
          updateMotion();
        }, {threshold:0});
        regions.forEach(region => observer.observe(region));
      }
      document.addEventListener('visibilitychange', updateMotion);
      if (reducedMotion.addEventListener) reducedMotion.addEventListener('change', updateMotion);
      else if (reducedMotion.addListener) reducedMotion.addListener(updateMotion);
      applyTheme(themePreference() || 'dark');
      apply(preference() || 'en');
      showCode(selectedCode);
      updateMotion();
    })();
