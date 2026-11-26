# Checklist Phát Hành VieForge Lên GitHub (An Toàn & Chuẩn Quốc Tế)

## Bước 1 — Bảo mật tài khoản GitHub cá nhân (2 phút)
1. Vào `https://github.com/settings/emails`:
   - Tick **Keep my email addresses private** (ẩn Gmail cá nhân khỏi lịch sử commit công khai).
   - Tick **Block command line pushes that expose my email**.
2. Vào `https://github.com/settings/security`:
   - Bật **Two-factor authentication (2FA)** (dùng app Authenticator hoặc Passkey).

## Bước 2 — Tạo GitHub Organization `vieforge` (Miễn phí)
1. Mở `https://github.com/organizations/plan` → chọn gói **Free ($0/month)** → bấm **Create a free organization**.
2. Điền:
   - **Organization account name:** `vieforge`
   - **Contact email:** email của bạn
   - **This organization belongs to:** *My personal account* (sau này chuyển sang *A business* lúc nào cũng được).
3. Hoàn tất xác minh (nếu GitHub yêu cầu CAPTCHA, bạn tự bấm trực tiếp trên Chrome).

## Bước 3 — Tạo Repository Public đầu tiên
1. Trong Organization `vieforge`, bấm **New repository**:
   - **Repository name:** `vieforge` *(hoặc `harness` — luôn dùng chữ thường)*.
   - **Description:** `3-Tier Hybrid AI Agent Harness (MCP + Ollama + SQLite) — Cut Cloud Agent token burn by 70-90%. Code First, Local LLM Second, Cloud Last.`
   - **Visibility:** **Public**
   - **KHÔNG** tick "Add a README" hay "Add .gitignore" (vì thư mục local đã có sẵn đầy đủ).
2. Sau khi tạo xong, vào mục **About (bánh răng góc phải)** gắn các **Topics**:
   `ai-agents`, `mcp`, `ollama`, `claude-code`, `cursor`, `codex`, `antigravity`, `token-optimization`, `local-llm`, `vietnam`.

## Bước 4 — Bật bảo mật cho Repository (Miễn phí cho Public Repo)
1. Vào `Repo → Settings → Code security`:
   - Bật **Dependency graph**, **Dependabot alerts**, **Secret scanning**, và **Push protection** (tự động chặn nếu lỡ đẩy nhầm API key/token).
2. Vào `Repo → Settings → Rules → Rulesets` (hoặc `Branches`):
   - Bảo vệ nhánh `main` (chặn force-push, yêu cầu CI `TierForge CI` xanh trước khi merge PR).

## Bước 5 — Đẩy mã nguồn từ máy lên GitHub
*(Cài Git for Windows tại `https://git-scm.com/download/win` hoặc dùng GitHub Desktop nếu máy chưa có `git`.)*

```bash
git init -b main
git add .
git commit -m "feat: initial v0.1.0 release of VieForge 3-Tier AI Agent Harness"
git remote add origin https://github.com/vieforge/vieforge.git
git push -u origin main
```
