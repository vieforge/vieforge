# VieForge 5分導入・評価ガイド（日本語版）

本ガイドでは、既存のPC環境やOS設定を一切変更せず、**費用ゼロ（`$0`）・外部APIキー不要** で VieForge の動作とトークン削減効果を確認する手順を説明します。

---

## 1. 前提条件

- **OS:** Windows 10/11、macOS（Apple Silicon / Intel）、Linux
- **Python:** 3.10 以上（`core/` は Python 標準ライブラリのみで動作します）
- **GPU:** 必須ではありません（CPUのみのノートPCでも動作します）

---

## 2. インストール（2つの方法）

### 方法A: AIエージェントに1文で任せる（おすすめ・初心者向け）

Antigravity・Claude Code・Cursor・Windsurf・Codex CLI を開き、以下の1文をそのまま入力してください。

> `https://github.com/vieforge/vieforge をクローンして AGENT_INSTALL.md を読み、python scripts/quick_setup.py を実行したうえで、結果とトークン削減レポート（Savings Receipt）をわかりやすい日本語で説明してください。`

### 方法B: PyPI から直接インストールする（エンジニア向け）

```bash
pip install vieforge
vieforge setup
```

※ 環境を汚したくない場合は、仮想環境（`python -m venv .venv` または `uv venv`）内へのインストールを推奨します。

---

## 3. 費用ゼロ（`$0`）で動作検証する

外部APIキーもローカルLLMも未設定のまま、オフラインで全機能を確認できます。

```bash
# 1. 環境診断（読み取り専用・OS設定は一切変更しません）
vieforge doctor

# 2. 0.3秒トークン削減ベンチマーク（お手元のPCで数値を再現）
vieforge bench

# 3. 障害中断 → SQLiteチェックポイント再開デモ（オフライン --mock-llm）
python examples/01_batch_doc_processor/run_demo.py --dry-run
python examples/01_batch_doc_processor/run_demo.py --reset --mock-llm --simulate-interrupt-after 2
python examples/01_batch_doc_processor/run_demo.py --mock-llm

# 4. プロジェクト単位の実測削減レポート（HTML出力）
vieforge savings --html savings_report.html
```

---

## 4. 利用するLLMの切り替え（11プリセット + 自動検出）

VieForge は環境変数 `TIERFORGE_LLM_PROVIDER` に **1単語** を設定するだけで、ローカルLLM・主要クラウドAPIのどちらにも接続できます。

| `TIERFORGE_LLM_PROVIDER` | 接続先 | 自動読み込みするAPIキー環境変数 | 備考 |
| :--- | :--- | :--- | :--- |
| `auto` | ローカルサーバーを自動検出 | 不要 | Ollama → LM Studio → llama.cpp → vLLM → Jan の順に自動検出 |
| `ollama` | `http://127.0.0.1:11434` | 不要 | ネイティブAPI（`keep_alive=0` で処理後にVRAMを自動解放） |
| `lmstudio` | `http://127.0.0.1:1234/v1` | 不要 | OpenAI互換 |
| `llamacpp` | `http://127.0.0.1:8080/v1` | 不要 | OpenAI互換 |
| `vllm` | `http://127.0.0.1:8000/v1` | 不要 | OpenAI互換 |
| `jan` | `http://127.0.0.1:1337/v1` | 不要 | OpenAI互換 |
| `gemini` | Google Gemini API | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | OpenAI互換エンドポイント直接対応 |
| `anthropic`（または `claude`） | Anthropic Messages API | `ANTHROPIC_API_KEY` | プロキシ不要で `/v1/messages` に直接接続 |
| `openai` | OpenAI API | `OPENAI_API_KEY` | `o1`/`o3`/`gpt-5` の `temperature` 制約に自動対応 |
| `deepseek` | DeepSeek API | `DEEPSEEK_API_KEY` | `<think>...</think>` ブロック自動除去対応 |
| `groq` | Groq API | `GROQ_API_KEY` | 高速推論向け |
| `openrouter` | OpenRouter API | `OPENROUTER_API_KEY` | 複数モデルの統一窓口 |

---

## 5. 対応AI IDE / エージェントへの組み込み

```bash
# 例: ご利用のIDEに合わせてMCP・スキル設定を自動生成
python scripts/install_skill.py --target antigravity
python scripts/install_skill.py --target claudecode
python scripts/install_skill.py --target cursor
python scripts/install_skill.py --target codeium      # Windsurf / Codeium
python scripts/install_skill.py --target vscode       # VS Code Copilot (.vscode/mcp.json)
python scripts/install_skill.py --target cline
```

Antigravity 2.0 をお使いの場合は、サイドペインUIプラグイン **VieForge Lens**（日本語・英語・ベトナム語・ドイツ語・韓国語対応）により、削減率やローカルAIの状態を画面右上で常時確認できます。

---

## 6. 完全アンインストール手順（クリーン削除）

評価後に VieForge を完全に削除したい場合、OSレジストリやシステム設定に痕跡は一切残りません。以下の手順のみで元に戻ります。

```bash
# 1. Pythonパッケージの削除
pip uninstall -y vieforge

# 2. プロジェクト内のローカル状態DB（必要な場合のみ）を削除
#    data/ ディレクトリ配下の SQLite ファイルのみを削除すれば完了です。
```

IDE側の設定も戻したい場合は、該当IDEのMCP設定ファイルから `vieforge` / `tierforge` エントリを削除するだけで完全に復元されます。
