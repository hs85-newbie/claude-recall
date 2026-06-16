# my-claude-global

Claude Code 전역 설정 중앙 관리 레포. 로컬(`~/.claude/`)과 클라우드(GitHub Actions)에서 동일한 규칙을 적용하고, Claude Code 세션 로그를 자체 RAG/위키로 적재하는 파이프라인을 관리합니다.

> **이력**: 초기엔 Paperclip(태스크 관리) · OpenSpace(스킬 학습 DB) 인프라와 연동했으나 두 서비스 종료(2026-06)로 제거됨. 현재는 **세션 아카이브 ingest 파이프라인**이 핵심이며, 특정 인프라에 묶이지 않고 로컬/임의 호스트에서 독립 구동됩니다.

---

## 구성 요소

| 구성 | 위치 | 역할 |
|---|---|---|
| **부트스트랩** | `bootstrap.sh` | 신규 시스템에 아래 전부를 한 번에 설치 |
| **전역 규칙** | `CLAUDE.md` + `docs/` | 로컬·클라우드 공통 개발 원칙 (단일 진실 소스) |
| **전역 설정** | `settings.json`(템플릿) + `hooks/` + `agents/` | 권한·모델·hooks·custom agents (머신독립 placeholder) |
| **외부 스킬팩** | gstack · im-not-ai(Humanize KR) | bootstrap이 clone+install 오케스트레이션 |
| **로컬 LLM** | `scripts/setup-local-llm.sh` | 스펙 감지 → LM Studio 모델 구성 (opt-in) |
| **세션 아카이브 파이프라인** | `tools/session-archive/` + `skills/` | 세션 JSONL → SQLite → 요약 → Obsidian 위키 |
| **GitHub Actions** | `.github/workflows/` | `@claude` 실행, PR 자동 리뷰, 야간 작업, 설정 동기화 체크 |

---

## 새 시스템 설치 (부트스트랩)

신규 머신에서 clone 후 `bootstrap.sh` 한 번이면 전역 환경이 구성됩니다 (macOS/Linux, 멱등).

```bash
git clone https://github.com/hs85-newbie/claude-recall.git
cd claude-recall && ./bootstrap.sh
export ANTHROPIC_API_KEY=sk-ant-...         # 요약 단계용 (또는 ~/.env)
# (선택) 크로스머신 회상: export LLM_WIKI_REMOTE=git@github.com:you/your-vault.git
# (선택) 한글 humanize:   export IM_NOT_AI_REMOTE=git@github.com:you/im-not-ai.git
```

### 전제조건 (Prerequisites)

`bootstrap.sh`는 시작 시 아래를 자동 점검하고, 빠진 게 있으면 설치 안내를 출력한 뒤 중단합니다. (도구 자체를 설치해 주지는 않음)

| 도구 | 용도 | 설치 |
|---|---|---|
| `git` | clone | `xcode-select --install` (mac) / 패키지 매니저 |
| `python3` **3.11+** | session-archive·settings 렌더 | `brew install python@3.12` / `apt install python3` |
| `bun` | gstack 빌드 | `curl -fsSL https://bun.sh/install \| bash` |
| `node` | gstack·local-llm MCP | `brew install node` / nodejs.org |
| `claude` | Claude Code 본체 (+로그인) | Claude Code CLI 설치 후 `claude /login` |

- 네트워크 필요(gstack·im-not-ai clone). **macOS / Linux 전용**(bash) — Windows는 WSL.
- **에이전트에게 시키지 말고 사용자가 직접 실행**: `bootstrap.sh`는 `~/.claude/settings.json`을 갱신하므로, AI 에이전트가 실행하면 자가수정 가드에 막힐 수 있음. 터미널에서 `./bootstrap.sh`로 실행.

### 설치 후 (Post-install)

1. `export ANTHROPIC_API_KEY=...` 또는 `~/.env`에 기록 (요약 단계 필수)
2. Claude Code 재시작 → 설정·스킬·에이전트 로드 확인
3. (선택) 로컬 LLM: `./bootstrap.sh --local-llm` + LM Studio 실행. full `local-llm` MCP는 `~/gemma4-bench` 별도 clone 필요

`bootstrap.sh`가 하는 일:

| 단계 | 내용 |
|---|---|
| 1 | `~/.claude/CLAUDE.md` → 레포 심링크 |
| 2 | `settings.json` 렌더링(`__HOME__` 치환) → `~/.claude/settings.json` (기존 백업, `settings.local.json`은 보존) |
| 3 | `hooks/` 배치 (route-to-local, local-llm-gate) |
| 4 | custom `agents/` 배치 (coder·explore·plan·local-ops·quick-lookup) |
| 5 | session-archive 스킬 배치 |
| 6 | gstack clone + `./setup --host claude` (스킬 일괄 설치) |
| 7 | im-not-ai(Humanize KR) clone + `install.sh` (humanize 스킬·에이전트) |
| 8 | `tools/session-archive/scripts/setup.sh` 위임 (venv + 스케줄러) |
| 9 | (opt-in `--local-llm`) 머신 스펙 감지 → 로컬 LLM 구성 |

### 로컬 LLM (스펙 자동 감지)

`./bootstrap.sh --local-llm` 또는 `scripts/setup-local-llm.sh` 단독 실행 시, RAM·아키텍처를 감지해 적합한 모델을 LM Studio(`lms` CLI)로 내려받고 `settings.json`에 반영합니다.

| RAM | 권장 모델(기본값) |
|---|---|
| < 16GB | `qwen/qwen3-4b` |
| 16–32GB | `google/gemma-4-26b-a4b` |
| 32–64GB | `qwen/qwen3-32b` |
| ≥ 64GB | `openai/gpt-oss-20b` |

- Apple Silicon은 MLX 런타임 자동 선택. `LOCAL_LLM_MODEL=...`로 모델 강제 지정 가능.
- `lms` 미설치 환경은 안내만 출력(ollama 등 대체 가능). 다운로드는 수 GB라 **기본 비활성(opt-in)**.

> **머신독립 처리**: `local-llm` MCP는 `~/gemma4-bench`가 없으면 자동 제외되고, hooks(LM Studio 의존)는 없는 환경에서 클라우드로 폴백합니다. 머신별 권한 누적분(`settings.local.json`)은 건드리지 않습니다.

### 보안 메모

레포 시크릿 스캔: **하드코딩된 키·토큰·DB 크리덴셜 0건** (히스토리 매치는 마스킹 테스트의 가짜 키뿐). 레포는 **PRIVATE**. 운영 시 인지할 강화 포인트:

- `settings.json`의 `skipAutoPermissionPrompt`·`skipDangerousModePermissionPrompt`(=true)가 bootstrap으로 **모든 신규 머신에 전파**됨 — 권한 프롬프트 자동 수락. 의도된 설정이나 새 환경에선 영향 범위 확인 권장.
- `claude-dispatch.yml`은 `--dangerously-skip-permissions` + `repository_dispatch` 트리거 — PAT(`GLOBAL_SETTINGS_TOKEN`) 유출 시 러너 임의 실행 가능. 토큰 스코프 최소화·로테이션 권장.
- bootstrap이 gstack·im-not-ai를 네트워크에서 clone 후 setup 스크립트 실행 — 공급망 신뢰 전제(공식 레포만 사용).

---

## 세션 아카이브 파이프라인 (핵심)

Claude Code가 `~/.claude/projects/**/*.jsonl`에 남기는 세션 로그를 자체 RAG/위키로 적재합니다. **외부 서비스 의존 없음** — Python(SQLite + FTS5) + Anthropic SDK만 필요하므로 어떤 호스트에서도 동작합니다.

```
~/.claude/projects/**/*.jsonl   (Claude Code 세션 로그)
        │  ① ingest  (mtime 증분, 시크릿 마스킹, FTS5 인덱싱)
        ▼
~/.claude-archive/sessions.db   (SQLite + FTS5 전문검색)
        │  ② summarize  (Haiku 요약)
        ▼
        │  ③ export-obsidian
        ▼
Obsidian vault  (RAG/위키)
```

| 단계 | 명령 | 비고 |
|---|---|---|
| ① 적재 | `session-archive ingest` | mtime 변경 파일만 증분. 첫 적재만 20분+, 이후 3분 내외. 로그 위치 다르면 `--root` 또는 `SESSION_ARCHIVE_ROOT` |
| ② 요약 | `session-archive summarize --mode haiku-only` | Anthropic SDK 필요 (`ANTHROPIC_API_KEY`) |
| ③ 내보내기 | `session-archive export-obsidian` | Obsidian vault로 마크다운 생성 (`--vault`, 기본 `~/llm-wiki`) |

### 설치 + 자동 실행 (권장)

`scripts/setup.sh`가 venv 설치 + OS 감지 스케줄러 등록(매일 **04:00**)을 한 번에 처리합니다.

```bash
tools/session-archive/scripts/setup.sh
# macOS → launchd 등록 / Linux → cron 등록
# 로그: ~/.claude-archive/launchd-pipeline.log
```

`bootstrap.sh`를 실행하면 이 단계는 자동 포함됩니다.

### 수동 실행

```bash
cd tools/session-archive
.venv/bin/session-archive ingest
.venv/bin/session-archive summarize --mode haiku-only
.venv/bin/session-archive export-obsidian
# 또는 전체 일괄
./scripts/pipeline.sh
```

> **이식성**: 경로는 모두 `$HOME`/스크립트 위치 기반(사용자명 하드코딩 없음). DB는 `SESSION_ARCHIVE_DB`, 입력은 `SESSION_ARCHIVE_ROOT`, vault는 `--vault`로 override. plist는 `*.plist.template`을 `setup.sh`가 머신별로 렌더링.

### 관련 스킬

| 스킬 | 역할 |
|---|---|
| `/session-archive-ingest` | 세션 JSONL 증분 적재 (대화에서 호출) |
| `/session-archive-stats` | 아카이브 현황 (세션/이벤트 수, 마스킹 통계, 파싱 에러) |

설계 배경은 `docs/adr/ADR-001-session-log-archive.md` 참조.

---

## 전역 규칙

`CLAUDE.md`가 슬림 코어, `docs/`가 상세 규칙(@import 참조)입니다.

```
CLAUDE.md                          # 전역 개발 원칙 (슬림 코어)
docs/
├── comment-rules.md               # 코드 주석 규칙
├── documentation-rules.md         # 문서화 규칙
├── git-rules.md                   # 형상관리 규칙
├── design-guide.md                # UI/UX 디자인 가이드
├── error-handling.md              # 에러 처리 패턴
├── api-standards.md               # API 설계 표준
├── testing-standards.md           # 테스트 전략
└── adr/ADR-001-session-log-archive.md
```

### 로컬 ↔ 클라우드 동기화

```
my-claude-global (GitHub, 단일 진실 소스)
    │
    ├── 로컬: ~/.claude/CLAUDE.md → 심링크 → ~/my-claude-global/CLAUDE.md
    │
    └── 클라우드: Actions 실행 시 checkout → ~/.claude/CLAUDE.md 복사
```

규칙 수정 시:
```bash
vi ~/my-claude-global/CLAUDE.md
cd ~/my-claude-global && git add . && git commit -m "docs: 규칙 추가" && git push
# 끝 — 다음 Actions 실행부터 자동 적용
```

---

## GitHub Actions

| 워크플로우 | 트리거 | 역할 |
|---|---|---|
| `claude.yml` | 이슈/PR에 `@claude` 댓글 | Claude Code 즉시 실행 |
| `claude-code-review.yml` | PR 생성/업데이트 | 자동 코드 리뷰 |
| `claude-dispatch.yml` | 수동 UI / `repository_dispatch` | 태스크 기반 Claude 실행 + 자동 커밋/푸시 |
| `claude-nightly.yml` | 매일 KST 23:00 | `claude-todo` 라벨 이슈 자동 처리 |
| `sync-check.yml` | 매주 월요일 KST 09:00 | 필수 파일/`@docs` 참조 검증, 문제 시 이슈 생성 |
| `monthly-review.yml` | 매월 1일 KST 09:00 | 월간 자동 점검 보고서 이슈 생성 |
| `ci-quality-gate.yml` | (프로젝트용 템플릿) | CI 품질 게이트 |

### GitHub Secrets (프로젝트 레포별)

| Secret | 등록 방법 | 용도 |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | `/install-github-app` 자동 등록 | Claude 인증 |
| `GLOBAL_SETTINGS_TOKEN` | 수동 등록 (PAT, repo 스코프) | 전역 설정 레포 접근 |

---

## 레포 구조

```
my-claude-global/
├── bootstrap.sh                   # 신규 시스템 전역 환경 설치 진입점
├── CLAUDE.md                      # 전역 개발 원칙 (슬림 코어)
├── settings.json                  # 설정 템플릿 (__HOME__ placeholder)
├── hooks/                         # route-to-local · local-llm-gate
├── agents/                        # custom agents (coder·explore·plan·local-ops·quick-lookup)
├── scripts/                       # setup-local-llm.sh (스펙 감지 로컬 LLM)
├── docs/                          # 상세 규칙 (@import 참조) + ADR
├── skills/
│   ├── session-archive-ingest/    # 세션 적재 스킬
│   └── session-archive-stats/     # 아카이브 현황 스킬
├── tools/session-archive/         # 적재·요약·내보내기 파이프라인 (Python)
│   ├── src/ · tests/
│   └── scripts/                   # pipeline.sh · setup.sh · *.plist.template
├── .github/workflows/             # GitHub Actions
└── project-template/              # 신규 프로젝트 템플릿 (별도 git repo)
```

> `project-template/`은 `.gitignore` 대상인 **별도 GitHub repo**입니다. 이 레포에서는 추적/커밋되지 않습니다.
