# セキュリティ・ライセンス・コンプライアンス仕様書

本資料は、企業の情報システム部門・セキュリティ監査担当者・法務ご担当者様に向けて、**VieForge** の設計上の安全保証・データ取り扱い・ライセンス透明性をまとめたものです。

---

## 1. OS非改変保証（Zero OS-Mutation）

VieForge はユーザー権限の範囲内のみで動作し、以下のシステム領域には **一切アクセス・変更を行いません**。

- Windows レジストリの読み書き・変更
- OSシステムサービス（Windows Service / systemd / launchd）の登録・停止
- ネットワークスタック・ファイアウォール・プロキシ設定の変更
- ページファイル・カーネルパラメータ・ドライバの変更

環境診断コマンド（`vieforge doctor` / `scripts/preflight_check.py`）も完全な読み取り専用です。

---

## 2. データ主権・ゼロテレメトリ

- **外部送信ゼロ:** VieForge 自体が開発元サーバーへ利用統計・ログ・ソースコード・クラッシュレポートを送信する機能（テレメトリ）は **一切実装されていません**。
- **100%ローカル完結:** SQLite WAL チェックポイント、SHA-256 重複排除、LLMレスポンスキャッシュ、CIログ圧縮、コスト台帳はすべてローカルディスク上で完結します。
- **ブラウザ連携ログの最小化（プロンプト非保存）:** 拡張機能ブリッジ（`core/extension_bridge.py`）のオフロード計測ログ（`data/bridge_offload_log.jsonl`）には、**サービス名とトークン件数のみ** を記録し、プロンプト原文・回答本文は一切保存しません。
- **閉域網（Air-Gapped）運用対応:** Ollama / LM Studio / llama.cpp / vLLM と組み合わせることで、インターネット遮断環境でも動作します。非root実行の `Dockerfile` および `compose.yml` を標準同梱しています。

---

## 3. シークレット自動秘匿（Secret Redaction）

ローカルコマンド出力やCIログを Tier 1 クラウドエージェントへ返す前に、`core/output_trimmer.py`（`redact_secrets=True` がデフォルト有効）が機密トークンを自動検出してマスクします。

主な自動秘匿対象パターン：
- OpenAI / 各種APIキー（`sk-...`）
- GitHub Personal Access Token（`ghp_...`, `github_pat_...`）
- Google APIキー（`AIza...`）
- AWS アクセスキー（`AKIA...`）
- HTTP `Authorization: Bearer ...` ヘッダーおよび環境変数形式のシークレット

---

## 4. 課金暴走防止（コスト上限ガード）

自律エージェントの無限ループによる予期せぬAPI高額請求を防ぐため、以下の決定論的ガードをコード層に内蔵しています。

1. **事前見積もり（`--dry-run`）:** 実行前に推定トークン数と推定USD金額を算出します。
2. **リトライ上限（`MAX_RETRY_PER_ITEM <= 2`）:** 同一項目での無限リトライを構造的に禁止します。
3. **二重予算上限（`MAX_JOB_USD` / `DAILY_USD_CAP`）:** 1ジョブ上限または日次上限に到達した時点で `BudgetExceededError` を発生させ、即座に処理を停止します。
4. **SQLite WAL チェックポイント:** 障害停止後も完了済み項目を再課金せず、中断箇所から `$0` で再開します。

---

## 5. ライセンス純潔性とサプライチェーン証明

| 監査項目 | 実装・保証内容 | ご自身での検証コマンド |
| :--- | :--- | :--- |
| **コアライセンス** | **MIT License**（商用利用・改変・社内配布可） | `LICENSE` を参照 |
| **外部依存ゼロ（`core/`）** | `core/` 配下は **100% Python標準ライブラリのみ** | `python scripts/audit_step.py --step verify --no-pytest` |
| **コピーレフト・制限ライセンス遮断** | `AGPL-3.0`・`BUSL-1.1`・`CC-BY-NC` 依存を自動検出・ブロック | `python -W error -m core.license_audit --root . --json` |
| **PyPI配布の来歴証明** | **OIDC Trusted Publishing** + **PEP 740 / SLSA Build Provenance** | PyPIページ（`pypi.org/project/vieforge/`）の Attestations を確認 |
| **コンテナ権限分離** | 非rootシステムユーザー（`vieforge`）での実行設計 | `Dockerfile` / `compose.yml` を参照 |

---

## 6. エンタープライズ向けガバナンス機能

- **RBAC・操作監査フック（`core/policy_guard.py`）:** コマンド実行・ツール呼び出しに対する許可/拒否ポリシーと監査ログ出力をプラグイン形式で組み込めます。
- **OpenTelemetry 準拠トレース（`core/qa_logger.py`）:** `emit_genai_span` により `gen_ai.*` 準拠のJSONLトレースを出力でき、既存の社内監視基盤（SIEM / Langfuse / Phoenix 等）へ連携しやすい設計です。

---

## 7. セキュリティ脆弱性のご報告窓口

- **セキュリティ専用窓口:** `security@vieforge.dev`
- **GitHub Private Security Advisories:** リポジトリの「Security」タブより非公開でご報告いただけます。
- **法人導入・コンプライアンス資料のご相談:** `hello@vieforge.dev`
