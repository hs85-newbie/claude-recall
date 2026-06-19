# 시작하기 — 새 컴퓨터에서 끝까지 따라하기

이 문서는 **새 컴퓨터에 Claude Code를 막 깔았고, 그 다음에 뭘 해야 할지 모르는 사람**을 위한 가이드입니다.
위에서부터 명령어를 **그대로 복사해서 붙여넣기만** 하면 끝납니다.
각 단계마다 **예상 출력**을 같이 적어 뒀으니, 비슷하게 나오면 정상입니다.
("이 정도는 알겠지" 같은 가정은 하지 않습니다.)

> claude-recall이 해주는 일 한 줄 요약: **Claude Code를 모든 컴퓨터에서 똑같은 규칙·설정으로 쓰게 만들고, 과거 대화를 검색·회상할 수 있게 해주는 전역 환경 + RAG 계층**입니다.

소요 시간: 처음이면 **20~40분** (대부분 도구 다운로드 대기 시간).

---

## 미리 알아둘 것 — 큰 그림 한 장

```
[0] 준비물 설치      →  git · python · node · bun · Claude Code · API 키
        │
[1] 레포 받고 bootstrap.sh 실행   →  규칙·설정·스킬·에이전트·파이프라인 한 번에 깔림
        │
[2] Claude Code 재시작            →  설정/스킬 로드
        │
[3] 파이프라인 첫 실행            →  지난 대화가 검색 가능해짐
        │
[4~] 검색 · 회상 · (선택) 크로스머신/로컬LLM
```

각 단계는 **앞 단계가 끝나야 다음으로** 넘어갑니다. 순서대로 진행하세요.

---

## 0단계 — 준비물 설치

`bootstrap.sh`(다음 단계)는 시작할 때 아래 도구가 다 있는지 자동으로 확인하고, **하나라도 없으면 안내만 하고 멈춥니다**(도구를 대신 깔아주지는 않음). 그래서 먼저 이 5가지를 깔아야 합니다.

> **OS 안내**: 이 가이드는 **macOS / Linux** 기준입니다. Windows는 WSL(Windows Subsystem for Linux) 안에서 진행하세요.

### 0-1. (macOS만) Homebrew 설치

Homebrew는 macOS에서 개발 도구를 깔아주는 "앱스토어" 같은 도구입니다. 이미 깔려 있으면 건너뛰세요(`brew --version`으로 확인).

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

설치 끝에 나오는 `Next steps:` 안내(터미널에 `eval "$(...)"` 한 줄 추가)를 그대로 따라 하세요. 안 하면 `brew` 명령을 못 찾습니다.

### 0-2. 도구 5종 설치

| 도구 | 확인 명령 | 없으면 (macOS) | 없으면 (Linux) |
|---|---|---|---|
| `git` | `git --version` | `xcode-select --install` | `sudo apt install git` |
| `python3` **3.11+** | `python3 --version` | `brew install python@3.12` | `sudo apt install python3` |
| `node` | `node --version` | `brew install node` | [nodejs.org](https://nodejs.org) |
| `bun` | `bun --version` | `curl -fsSL https://bun.sh/install \| bash` | 동일 |
| `claude` | `claude --version` | (아래 0-3) | (아래 0-3) |

각 `확인 명령`을 쳤을 때 버전 숫자가 나오면 그 도구는 OK입니다. `command not found`가 나오면 옆 칸 설치 명령을 실행하세요.

> `python3 --version`이 `3.11` 미만이면 안 됩니다(`session-archive`가 3.11+ 필요). `brew install python@3.12` 후 새 터미널을 여세요.

### 0-3. Claude Code 설치 + 로그인

이미 깔았다면 로그인만 확인하세요.

```bash
# 설치 (이미 했으면 생략)
npm install -g @anthropic-ai/claude-code

# 버전 확인
claude --version

# 로그인 (브라우저가 열림 → Anthropic 계정 로그인)
claude
# 처음 실행 시 /login 안내가 나옵니다. 화면 지시를 따르세요.
```

### 0-4. Anthropic API 키 발급

이 환경의 **요약(summarize) 단계**가 Anthropic API를 씁니다. (Claude Code 로그인과는 별개의 키입니다.)

1. [console.anthropic.com](https://console.anthropic.com) 접속 → 로그인
2. 왼쪽 **API Keys** → **Create Key** → 키 복사 (`sk-ant-...`로 시작)
3. 키를 환경에 등록 — 둘 중 하나:

```bash
# 방법 A) 이번 터미널 세션에만 (테스트용)
export ANTHROPIC_API_KEY=sk-ant-여기에-복사한-키

# 방법 B) 영구 — .env 파일에 기록 (권장)
#   먼저 템플릿 복사: cp .env.example .env  → 그 안에 키 채우기
#   (.env는 git에 안 올라갑니다 — gitignore 처리됨)
echo 'ANTHROPIC_API_KEY=sk-ant-여기에-복사한-키' >> ~/.env
```

> 키가 없어도 1~3단계 설치는 됩니다. 다만 **요약 단계에서 인증 에러**가 납니다. 미리 넣어두는 게 편합니다.

✅ **0단계 체크**: `git --version`, `python3 --version`(3.11+), `node --version`, `bun --version`, `claude --version`이 전부 버전 숫자를 출력하면 다음으로.

---

## 1단계 — 레포 받고 bootstrap 실행

> ⚠️ **중요 — AI한테 시키지 말고 본인이 터미널에서 직접 실행하세요.**
> `bootstrap.sh`는 `~/.claude/settings.json`(Claude의 설정 파일)을 고칩니다. AI 에이전트가 이걸 실행하면 "자기 설정을 자기가 바꾸는" 안전장치에 막힙니다. **꼭 사람이 터미널에 직접** 치세요.

```bash
git clone https://github.com/hs85-newbie/claude-recall.git
cd claude-recall
./bootstrap.sh
```

**예상 출력** (요지 — 숫자·경로는 환경마다 다름):

```
[bootstrap] 전제조건 충족 (git·python3.11+·bun·node·claude)
[bootstrap] CLAUDE.md → /Users/you/claude-recall/CLAUDE.md 심링크 생성
[bootstrap] settings.json 렌더링 완료 (백업: settings.json.bak-20260619-153000)
[bootstrap] hooks 배치: 2개
[bootstrap] custom agents 배치: 5개
[bootstrap] session-archive 스킬 배치
[bootstrap] gstack clone...
[bootstrap] gstack 스킬 설치 완료
[bootstrap] im-not-ai 생략 — 쓰려면 export IM_NOT_AI_REMOTE=git@... 후 재실행
[bootstrap] session-archive 파이프라인 설치 위임...
[bootstrap] vault 생략 — LLM_WIKI_REMOTE 미설정(크로스머신 회상 쓰려면 export LLM_WIKI_REMOTE=git@...)
[bootstrap] 로컬 LLM 구성 생략 — 원하면 별도 실행: scripts/setup-local-llm.sh
[bootstrap] 완료
```

**이 한 줄이 무슨 일을 한 건가** (`bootstrap.sh`가 깐 것들):

| 깐 것 | 무슨 역할 |
|---|---|
| `CLAUDE.md` 심링크 | 전역 개발 규칙 (모든 프로젝트 공통) |
| `settings.json` | Claude 권한·모델·hooks 설정 |
| `hooks/`, `agents/` | 자동화 훅 2개 + 전용 에이전트 5개(coder·explore·plan 등) |
| gstack 스킬 | `/ship`, `/qa`, `/review` 등 강력한 워크플로 스킬 모음 |
| session-archive | 과거 대화를 검색 가능하게 적재하는 RAG 파이프라인 |
| im-not-ai *(선택)* | 한글 글 다듬는 Humanize 스킬 — `IM_NOT_AI_REMOTE` 설정 시에만 |

> **멱등(idempotent)**: `bootstrap.sh`는 몇 번을 다시 돌려도 안전합니다. 기존 설정은 `.bak-날짜`로 백업하고 갱신합니다. 중간에 실패하면 원인(보통 도구 누락) 고치고 그냥 다시 실행하세요.

✅ **1단계 체크**: 마지막에 `[bootstrap] 완료`가 보이면 OK.

---

## 2단계 — Claude Code 재시작 + 확인

설정·스킬·에이전트는 **Claude Code를 다시 켜야** 로드됩니다.

1. 실행 중인 Claude Code를 완전히 종료
2. 다시 실행: `claude`
3. 확인: 아래를 Claude에게 쳐보세요.

```
/help
```

스킬 목록에 `/ship`, `/qa`, `/review`, `/session-archive-ingest` 같은 게 보이면 정상입니다.

✅ **2단계 체크**: 스킬 목록에 `session-archive` 관련 항목이 보이면 OK.

---

## 3단계 — 세션 아카이브 파이프라인 첫 실행

`bootstrap.sh`가 `tools/session-archive`에 파이썬 환경(venv)을 만들어 뒀습니다. 이 파이프라인이 **지난 Claude 대화를 검색 가능하게** 만들어 줍니다. 3개 단계입니다.

```bash
cd ~/claude-recall/tools/session-archive

# ① 적재: 대화 로그(JSONL) → 검색용 DB (비밀번호·키는 자동 마스킹)
.venv/bin/session-archive ingest
```

**예상 출력** (숫자는 환경마다 다름):

```
[ingest] root=/Users/you/.claude/projects
[ingest] scanned 312 files, 47 changed
[ingest] sessions +47, events +18,204, masked 31 secrets
[ingest] done in 142s
```

> 첫 적재는 데이터가 많으면 **20분 이상** 걸릴 수 있습니다. 두 번째부터는 바뀐 것만 처리해서 3분 내외입니다.

```bash
# ② 요약: 각 대화를 짧게 요약 (← 여기서 ANTHROPIC_API_KEY 사용)
.venv/bin/session-archive summarize --mode haiku-only

# ③ 내보내기: 요약을 Obsidian 위키(마크다운)로 (기본 위치 ~/llm-wiki)
.venv/bin/session-archive export-obsidian
```

> **한 번에 전부**: `./scripts/pipeline.sh` 를 치면 ①②③ + 동기화까지 한 방에 돕니다.
> **자동 실행**: `bootstrap.sh`가 **매일 04:00 자동 실행**을 이미 등록했습니다(macOS launchd / Linux cron). 즉 한 번 깔면 이후엔 알아서 쌓입니다.

✅ **3단계 체크**: `ingest`가 `done`으로 끝나면 OK.

---

## 4단계 — 검색 / 현황 보기

```bash
# 현황: 얼마나 쌓였나
.venv/bin/session-archive stats
```

**예상 출력**:

```
sessions: 359   events: 1,204,118   masked: 1,042
parse errors: 0
top projects:
  -Users-you-acme-app        128 sessions
  -Users-you-claude-recall     74 sessions
```

```bash
# 검색: 과거 대화 전문검색 (--since 기간, --project 프로젝트 필터)
.venv/bin/session-archive search "결제 정책" --since 30d --limit 5
```

대화 중에는 스킬로도 됩니다: `/session-archive-ingest`(적재), `/session-archive-stats`(현황).

---

## 5단계 — Claude가 스스로 과거를 회상하게 (MCP)

`bootstrap.sh`가 회상용 MCP 서버를 `settings.json`에 등록했습니다. **Claude Code를 재시작**하면(2단계에서 했으면 됨), Claude가 아래 4개 도구로 과거를 **스스로** 뒤져서 답합니다.

| 도구 | 용도 |
|---|---|
| `search_history` | 지난 대화 전문검색 |
| `recall_decisions` | 과거에 내린 결정 회상 |
| `recent_checkpoints` | "다음에 할 일" 체크포인트 |
| `search_vault` | **다른 노트북**에서 내린 결정까지 (크로스머신) |

**확인 방법**: Claude Code에서 이렇게 물어보세요.

```
지난주에 결제 모듈 어떻게 처리했었지?
```

Claude가 `search_history`를 호출해 과거 대화를 근거로 답하면 정상입니다.

---

## 6단계 — (선택) 크로스머신 회상

여러 대 컴퓨터를 쓰고, **다른 노트북에서 내린 결정까지** 이 컴퓨터에서 회상하고 싶을 때만 하세요.

```bash
# 본인 vault(위키) git 저장소 주소를 등록
export LLM_WIKI_REMOTE=git@github.com:you/your-vault.git

.venv/bin/session-archive vault-push     # 이 컴퓨터 요약을 vault로 올림
.venv/bin/session-archive sync-vault     # 다른 컴퓨터 결정을 내려받음
```

> 영구 적용은 `.env`에 `LLM_WIKI_REMOTE=...` 한 줄 추가 후 `bootstrap.sh` 재실행.
> 설계 배경: `docs/adr/ADR-002-rag-llm-wiki-architecture.md`.

---

## 7단계 — (선택) 로컬 LLM

요약을 클라우드 API 대신 **내 컴퓨터의 로컬 모델**로 돌리고 싶을 때만. 다운로드가 수 GB라 기본은 꺼져 있습니다.

```bash
./bootstrap.sh --local-llm
```

컴퓨터 RAM을 감지해 적당한 모델을 LM Studio로 내려받습니다.

| RAM | 자동 선택 모델 |
|---|---|
| < 16GB | `qwen/qwen3-4b` |
| 16–32GB | `google/gemma-3-12b` |
| 32–64GB | `qwen/qwen3-32b` |
| ≥ 64GB | `openai/gpt-oss-20b` |

> Apple Silicon(M칩)은 MLX 런타임 자동 선택. `LOCAL_LLM_MODEL=...`로 모델 강제 지정 가능.

---

## 일상 사용 — 매일 뭘 하면 되나

설치가 끝나면 **사실상 아무것도 안 해도 됩니다**. 매일 04:00에 파이프라인이 알아서 돕니다.

- **그냥 Claude Code를 평소처럼 쓰세요.** 모든 대화가 자동으로 쌓이고, 다음날 검색 가능해집니다.
- **과거가 궁금하면** Claude에게 자연어로 물어보세요("저번에 ~ 어떻게 했지?"). MCP가 알아서 회상합니다.
- **gstack 스킬**을 적극 쓰세요: `/ship`(배포), `/qa`(테스트), `/review`(코드 리뷰), `/investigate`(버그 추적), `/context-save`·`/context-restore`(작업 상태 저장/복원).
- **규칙을 바꾸고 싶으면** `~/claude-recall/CLAUDE.md`를 고치고 commit/push 하세요. 심링크라 즉시 반영됩니다.

---

## 자주 막히는 곳 (Troubleshooting)

| 증상 | 원인 / 해결 |
|---|---|
| `bootstrap.sh`가 "전제조건"에서 멈춤 | 0단계 도구 중 하나가 없음. 출력에 적힌 도구 설치 후 재실행(멱등이라 안전) |
| `summarize`가 인증 에러 | `ANTHROPIC_API_KEY` 미설정. `.env` 또는 `~/.env`에 키 한 줄 추가 |
| 세션 로그를 못 찾음 | 로그 위치가 기본과 다름. `--root` 또는 `SESSION_ARCHIVE_ROOT` 환경변수로 지정 |
| MCP 회상이 안 됨 | Claude Code 재시작을 안 함. 완전 종료 후 다시 실행 |
| `~/.claude/settings.json` 권한 가드에 막힘 | bootstrap을 AI에게 시킴. **사람이 터미널에서 직접** 실행 |
| `command not found: brew` | Homebrew 설치 후 안내(`eval "$(...)"`)를 안 따름. 안내대로 PATH 등록 |
| 스케줄러(자동 실행) 로그 확인 | `~/.claude-archive/launchd-pipeline.log` |

---

## 어디에 뭐가 깔렸나 (참고)

| 위치 | 내용 |
|---|---|
| `~/.claude/CLAUDE.md` | 전역 규칙 (레포로 심링크) |
| `~/.claude/settings.json` | Claude 설정 (기존은 `.bak-날짜`로 백업됨) |
| `~/.claude/skills/`, `agents/`, `hooks/` | 스킬·에이전트·훅 |
| `~/gstack/` | gstack 스킬 본체 |
| `~/.claude-archive/sessions.db` | 적재된 대화 검색 DB |
| `~/llm-wiki/` | Obsidian 위키(요약 export 대상) |

---

## 더 읽기

- 전체 개요·구조: `README.md`
- 전역 규칙: `CLAUDE.md` (+ `docs/`)
- 설계 배경(왜 이렇게 만들었나): `docs/adr/ADR-001-session-log-archive.md`, `docs/adr/ADR-002-rag-llm-wiki-architecture.md`
