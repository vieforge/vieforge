# 導入評価チェックリスト & よくあるご質問（法人・開発チーム向け）

日本の開発現場・情報システム部門での **導入前検証（PoC）** および **社内稟議書への添付** にそのままご活用いただけるチェックリストと詳細FAQです。

---

## 1. 導入前10項目検証チェックリスト（再現コマンド付き）

以下のコマンドを評価用PCで実行するだけで、安全性・ライセンス・削減効果のすべてを客観的に裏付けられます。

| # | 検証項目 | 実行コマンド | 合格基準 |
| :-: | :--- | :--- | :--- |
| 1 | Python / SQLite WAL / GPU 環境確認 | `vieforge doctor --json` | `"overall": "PASS"` |
| 2 | コアのライセンス純潔性監査 | `python -W error -m core.license_audit --root . --json` | `"passed": true`, `"findings": []` |
| 3 | 自動テスト全件パス（オフライン `$0`） | `pytest -q` | 全テスト PASS（失敗 0件） |
| 4 | 0.3秒トークン削減ベンチマーク | `vieforge bench` | 純削減率 `-90%` 以上（初回1,459トークン込み） |
| 5 | 障害中断 → 再開の冪等性確認 | `python examples/01_batch_doc_processor/run_demo.py --reset --mock-llm --simulate-interrupt-after 2` 後に再実行 | 完了済み2件が再実行されず残り3件のみ処理 |
| 6 | シークレット自動秘匿の確認 | `pytest -q -k redact` | `sk-`, `ghp_`, `Bearer` 等がマスクされる |
| 7 | 予算上限ガード（`BudgetExceededError`） | `pytest -q tests/test_cost_guard.py` | 上限超過時に確実に停止 |
| 8 | 複数LLMプロバイダ・推論モデル対応 | `pytest -q tests/test_llm_router.py` | 全テスト PASS（Claude / Gemini / `<think>` 含む） |
| 9 | ブラウザ連携オフロード計測 & 非保存確認 | `pytest -q tests/test_savings_report.py` | トークン分離計測 PASS・プロンプト本文のログ非保存を確認 |
| 10 | 非root Docker & クリーンアンインストール | `Dockerfile` の `USER vieforge` 確認 / `pip uninstall -y vieforge` | root権限不要・OS設定への残留ゼロ |

---

## 2. よくあるご質問（厳格な導入審査向けFAQ）

### Q1. 社内ソースコードや機密ログが外部AIに送信されるリスクはありますか？
**A.** VieForge の Tier 2 処理（SQLiteチェックポイント、CIログ圧縮、ローカルLLM推論、シークレット秘匿）は **100%ローカル完結** であり、VieForge 自体が外部サーバーへデータを送信することは一切ありません。Tier 1 クラウドAIエージェントへ返す要約テキストに対しても、標準でシークレット自動秘匿（`redact_secrets=True`）が適用されます。完全オフライン運用が必要な場合は、ローカルLLM（Ollama / LM Studio / llama.cpp / vLLM）のみで構成可能です。

### Q2. 夜間バッチなどでAIが暴走し、クラウドAPI費用が高額になる心配はありませんか？
**A.** ありません。`--dry-run` による事前見積もり、`MAX_RETRY_PER_ITEM <= 2` の再試行上限、`MAX_JOB_USD`（ジョブ上限）および `DAILY_USD_CAP`（日次上限）のハードリミットをコード層で強制しており、上限到達時は即座に停止します。

### Q3. 複数のプロジェクトを同時に動かすとGPUメモリ（VRAM）が衝突しませんか？
**A.** 衝突しない設計です。`core/concurrency.py` のOSレベル排他ロック（Windows `msvcrt` / macOS・Linux `fcntl`）、参照カウント式の **Ollama VRAMリース制御（`batch_session()`）**、および `exclusive_gpu=True` により、複数ワークスペースを並列稼働させても安全にVRAMを共有・解放します。

### Q4. 高価なGPUサーバーがないと使えませんか？
**A.** 不要です。Tier 0 の決定論的コード（CIログ91%圧縮、SQLite WALチェックポイント、SHA-256重複排除、予算ガード）は **GPUなし・CPUのみ** で数ミリ秒で動作します。ローカルLLMもCPU環境向け軽量モデル（`qwen2.5:3b` 等）や Apple Silicon 統合メモリ、8GB〜24GB+ NVIDIA GPU に自動適応します。

### Q5. DeepSeek-R1 や OpenAI o3 などの最新「推論モデル（Reasoning）」でも使えますか？
**A.** はい。`DeepSeek-R1`・`QwQ`・`Qwen3` などが出力する `<think>...</think>` 思考タグを自動除去してからJSON抽出を行うため、余計なパースエラーやリトライを防ぎます。また `o1`/`o3`/`gpt-5` 系の `temperature` パラメータ制約にも自動で対応します。

### Q6. すでに社内にある Gemini API や Anthropic Claude API キーをそのまま使えますか？
**A.** はい。`TIERFORGE_LLM_PROVIDER=gemini` または `TIERFORGE_LLM_PROVIDER=claude` を指定するだけで、プロキシサーバーを立てずに直接接続できます（`GEMINI_API_KEY` / `ANTHROPIC_API_KEY` を自動読み込み）。

### Q7. 商用プロジェクトや受託開発案件に組み込んでもライセンス上の問題はありませんか？
**A.** 問題ありません。コアエンジンは **MITライセンス** であり、`core/` は外部ライブラリを一切含まない **100% Python標準ライブラリ** で実装されています。さらに `python -m core.license_audit` により、`AGPL-3.0`・`BUSL-1.1`・`CC-BY-NC` などの商用リスクが高い依存関係の混入を自動で検知・遮断します。

### Q8. 無料版（MIT Core）と将来の法人向け拡張（Open-Core）の境界はどこですか？
**A.** 3層オーケストレーション、SQLite WALチェックポイント、11プロバイダ対応ルーター、シークレット秘匿、MCPサーバー、非root Docker、OpenTelemetry JSONL出力、ローカルRBACフック（`PolicyGuard`）などの **中核機能はすべてMITライセンスで永久無料** です。将来の法人向け商用アドオンは、SAML/OIDC SSO連携、複数ノードGPUワーカープール管理UI、専任SLAサポートなどの大規模組織向け管理機能に限定されます（詳細は [ENTERPRISE_ARCHITECTURE.md](../ENTERPRISE_ARCHITECTURE.md) をご参照ください）。

### Q9. Windows環境とmacOS/Linux環境で動作に差はありますか？
**A.** ありません。3 OS（Windows 11 / macOS / Ubuntu Linux）でのクロスOS検証およびクリーンwheel E2Eテストを通過しています。ファイルロック・パス解決・MCP設定生成も各OSに合わせて自動適応します。

### Q10. 法人での導入相談やセキュリティチェックシートへの回答依頼はどこへ連絡すればよいですか？
**A.** 以下までお気軽にご連絡ください。
- **法人導入・ライセンス・お見積り:** `hello@vieforge.dev`
- **セキュリティに関するお問い合わせ:** `security@vieforge.dev`
- **公式サイト:** https://vieforge.dev
