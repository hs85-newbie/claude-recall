# claude-recall

> **Claude Code의 기억을 어느 머신에서든 이어주는 전역 환경 + RAG 계층.**

> 🚀 **처음이세요?** 새 컴퓨터에서 전제도구 설치부터 일상 사용까지 한 단계씩 따라하는 가이드 → **[GETTING_STARTED.md](GETTING_STARTED.md)**

Claude Code는 세션이 끝나면 다 잊는다. 어제 그 라이브러리를 왜 골랐는지, 저 버그를 어떻게 잡았는지. 다음 대화엔 아무것도 안 남는다. claude-recall은 그 기억을 붙여준다.

clone하고 `bootstrap.sh` 한 번이면 둘이 깔린다. 어느 머신에서든 똑같이 재현되는 Claude Code 전역 환경(규칙·설정·hooks·스킬), 그리고 과거 세션·결정·체크포인트를 검색하고 회상하는 RAG 계층이다.

동작은 이렇다. Claude Code가 남기는 세션 로그(JSONL)를 로컬 SQLite로 적재하고(시크릿은 마스킹), Haiku로 요약해 Obsidian 마크다운으로 내보낸다. 그러면 MCP 서버가 그 인덱스를 Claude에게 도구로 노출한다. "예전에 이거 어떻게 했더라" 싶을 때 Claude가 직접 검색한다. vault를 git으로 동기화하면 다른 노트북에서 내린 결정까지 회상된다.

중앙 서버도 월정액도 없다. 벤더에 묶이지도 않는다. git 레포 하나에 로컬 Python(SQLite·FTS5)이 전부고, 어느 호스트에서나 독립으로 돈다.

MCP 도구는 네 가지다. `search_history`(세션 전문검색), `recall_decisions`(과거 결정 회상), `recent_checkpoints`(다음 할 일), `search_vault`(크로스머신 회상).

> `CLAUDE.md`는 오피니언 예시(개인 개발 원칙·한국어)다. 취향껏 갈아끼우면 된다.

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
| 3 | `hooks/` 배치 (route-to-local · local-llm-gate · git-safety-guard · session-handoff-load/save) |
| 4 | custom `agents/` 배치 (coder·explore·plan·local-ops·quick-lookup · critic · virtual-me) |
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

레포 시크릿 스캔: **하드코딩된 키·토큰·DB 크리덴셜 0건** (매치는 마스킹 테스트의 가짜 키·플레이스홀더뿐). 이 레포는 개인 환경에서 **탈개인화·fresh 히스토리로 떠낸 공개 스냅샷**이다 — 키·vault·개인 설정은 포함되지 않는다. 운영 시 인지할 강화 포인트:

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

## 스킬·에이전트·훅 사용법

이 레포가 더하는 기능은 세 가지 방식으로 작동하는데, 부르는 법이 저마다 다릅니다.

| 종류 | 부르는 법 | 한 줄 |
|---|---|---|
| **스킬** | `/이름` 슬래시 명령 **또는** 평범한 한국어 지시 (둘 다 됨) | 작업 절차를 묶은 워크플로 |
| **에이전트** | 한국어로 지목/위임 (슬래시 아님) | 별개 문맥에서 도는 전문 일꾼 |
| **훅** | **자동** (사용자가 부르지 않음) | 특정 시점에 끼어드는 자동 가드 |

### 스킬 — 슬래시 또는 자연어 (둘 다 됨)

스킬을 부르는 길은 둘입니다. (1) 슬래시 명령으로 직접 실행하거나, (2) 트리거 문구를 평범한 대화에 슬쩍 넣으면 Claude가 알아서 그 스킬을 띄웁니다. 어느 쪽이든 결과는 같습니다.

| 스킬 | 슬래시 | 자연어 예시 |
|---|---|---|
| `grill-me` | `/grill-me` | "빌드 전에 내 의도 좀 캐물어줘", "이 계획 압박테스트 해줘" |
| `handoff` | `/handoff` | "다음 세션 위해 인계 남겨줘", "세션 마무리 정리해줘" |
| `verify-layer` | `/verify-layer` | "이 결과물 제대로 됐는지 검증해줘", "납품 전 비판 패널 돌려줘" |
| `session-archive-ingest` | `/session-archive-ingest` | "세션 적재해줘" |
| `session-archive-stats` | `/session-archive-stats` | "아카이브 현황 보여줘" |

> 트리거 문구는 각 스킬 `SKILL.md` 상단의 description에 적혀 있습니다. 정확히 외울 필요 없이 비슷하게 말하면 잡힙니다.

### 에이전트 — 한국어로 지목 (슬래시 아님)

에이전트는 슬래시로 부르지 않습니다. 그냥 말로 시키면 메인 세션이 알맞은 에이전트에게 일을 넘기거나 자동으로 위임합니다.

| 에이전트 | 언제 / 어떻게 | 자연어 예시 |
|---|---|---|
| `virtual-me` | 되돌리기 어려운 결정 직전, "내 관점에서 봐줘" | "이거 나라면 승인할까?", "비용 관점에서 판단해줘" |
| `critic` | 산출물 적대적 검토 (주로 verify-layer가 자동 소환) | "이 코드 비판적으로 봐줘", "이 번역 반증해봐" |
| `coder`·`explore`·`plan`·`local-ops`·`quick-lookup` | 코드 수정·탐색·설계 (메인이 자동 위임) | "이 함수 구현해줘", "이 버그 어디서 나는지 찾아줘" |

> `virtual-me`는 어디까지나 초안자입니다. 판단을 그려줄 뿐 외부 발행·머지·결제를 '승인'하지는 않습니다. 최종 결정은 늘 사람 몫입니다.
> 페르소나는 고정이 아닙니다. 당신의 `CLAUDE.md`·`MEMORY.md`가 채워질수록 `virtual-me`·`grill-me`가 점점 당신 기준으로 판단합니다(처음엔 범용 → 쓸수록 당신처럼).

### 훅 — 자동 (부를 필요 없음)

훅은 설치되면 정해진 시점에 저절로 작동합니다. 명령도 지시도 필요 없습니다.

| 훅 | 작동 시점 | 하는 일 |
|---|---|---|
| `session-handoff-save.sh` | 세션 끝날 때 | git 상태 + 마지막 지시를 인계 메모로 자동 저장 |
| `session-handoff-load.sh` | 새 세션 시작할 때 | 직전 인계 메모를 자동으로 대화에 주입 (수동 restore 불필요) |
| `git-safety-guard.sh` | `git` 명령 실행 직전 | `reset --hard`·force push·`clean -f` 등 파괴적 명령을 가로채 확인창 띄움 |
| `route-to-local.sh` | "요약해/번역해" 등 입력 시 | 단순 작업을 로컬 LLM으로 라우팅 (설정 시) |
| `local-llm-gate.sh` | 로컬 LLM 호출 전 | 게이트 점검 |

> 인계 훅 둘이 짝입니다 — 끝낼 때 저장, 시작할 때 복원. 더 풍부한 인계가 필요하면 `/handoff`로 직접 덮어씁니다.

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
claude-recall (GitHub, 단일 진실 소스)
    │
    ├── 로컬: ~/.claude/CLAUDE.md → 심링크 → ~/claude-recall/CLAUDE.md
    │
    └── 클라우드: Actions 실행 시 checkout → ~/.claude/CLAUDE.md 복사
```

규칙 수정 시:
```bash
vi ~/claude-recall/CLAUDE.md
cd ~/claude-recall && git add . && git commit -m "docs: 규칙 추가" && git push
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
claude-recall/
├── bootstrap.sh                   # 신규 시스템 전역 환경 설치 진입점
├── CLAUDE.md                      # 전역 개발 원칙 (슬림 코어)
├── settings.json                  # 설정 템플릿 (__HOME__ placeholder)
├── hooks/                         # route-to-local · local-llm-gate · git-safety-guard · session-handoff-load/save
├── agents/                        # custom agents (coder·explore·plan·local-ops·quick-lookup · critic · virtual-me)
├── scripts/                       # setup-local-llm.sh (스펙 감지 로컬 LLM)
├── docs/                          # 상세 규칙 (@import 참조) + ADR
├── skills/
│   ├── grill-me/                  # 빌드 전 의도 심문 (정렬)
│   ├── handoff/                   # 세션 인계 메모 작성
│   ├── verify-layer/              # 결과물 검증 (비판 패널 + 3층 루브릭)
│   ├── session-archive-ingest/    # 세션 적재 스킬
│   └── session-archive-stats/     # 아카이브 현황 스킬
├── tools/session-archive/         # 적재·요약·내보내기 파이프라인 (Python)
│   ├── src/ · tests/
│   └── scripts/                   # pipeline.sh · setup.sh · *.plist.template
├── .github/workflows/             # GitHub Actions
└── project-template/              # 신규 프로젝트 템플릿 (별도 git repo)
```

> `project-template/`은 `.gitignore` 대상인 **별도 GitHub repo**입니다. 이 레포에서는 추적/커밋되지 않습니다.
