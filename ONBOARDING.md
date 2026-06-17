# ONBOARDING — claude-recall 처음 설치하기

이 문서는 **claude-recall을 처음 보는 사람**이 자기 노트북에서 끝까지 굴려보도록 돕습니다.
명령어와 **예상 출력**을 함께 적었으니, 출력이 비슷하게 나오면 정상입니다.
("이미 알겠지" 가정 없이 한 단계씩 진행합니다.)

claude-recall이 해주는 일 한 줄 요약: **Claude Code 세션 기억을 검색·회상 가능하게 만드는 전역 환경 + RAG 계층**.

---

## 0. 미리 준비 (Prerequisites)

아래가 깔려 있어야 합니다. `bootstrap.sh`가 시작할 때 자동 점검하고, 없으면 안내 후 멈춥니다(도구를 대신 설치해 주지는 않음).

| 도구 | 확인 명령 | 없으면 |
|---|---|---|
| `git` | `git --version` | `xcode-select --install` (mac) |
| `python3` 3.11+ | `python3 --version` | `brew install python@3.12` |
| `bun` | `bun --version` | `curl -fsSL https://bun.sh/install \| bash` |
| `node` | `node --version` | `brew install node` |
| `claude` | `claude --version` | Claude Code CLI 설치 후 `claude /login` |

> **OS**: macOS / Linux 전용(bash). Windows는 WSL에서.

요약 단계(2단계)에서 Anthropic API 키가 필요합니다. 미리 준비:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# 또는 ~/.env 파일에 ANTHROPIC_API_KEY=... 한 줄
```

---

## 1. 설치 (bootstrap)

> **중요**: AI 에이전트한테 시키지 말고 **터미널에서 직접** 실행하세요. `bootstrap.sh`는 `~/.claude/settings.json`을 고치기 때문에, 에이전트가 실행하면 자가수정 가드에 막힐 수 있습니다.

```bash
git clone https://github.com/hs85-newbie/claude-recall.git
cd claude-recall
./bootstrap.sh
```

예상 출력(요지):

```
[bootstrap] prerequisites OK (git, python3, bun, node, claude)
[1/8] ~/.claude/CLAUDE.md → 심링크 생성
[2/8] settings.json 렌더링 → ~/.claude/settings.json (기존 백업)
[3/8] hooks 배치
[4/8] agents 배치
[5/8] session-archive 스킬 배치
[6/8] gstack clone + setup
[7/8] im-not-ai clone + install     # IM_NOT_AI_REMOTE 미설정 시 건너뜀
[8/8] session-archive setup (venv + 스케줄러)
[bootstrap] done. Claude Code를 재시작하세요.
```

설치 후:

1. Claude Code를 **재시작** (설정·스킬·에이전트 다시 로드)
2. `ANTHROPIC_API_KEY` 준비 확인(0단계)

---

## 2. 세션 아카이브 파이프라인 돌려보기

`bootstrap.sh`가 `tools/session-archive`에 Python venv를 만들어 둡니다. 세 단계로 돕니다.

```bash
cd tools/session-archive

# ① 적재: Claude Code 세션 로그(JSONL) → SQLite (시크릿 마스킹)
.venv/bin/session-archive ingest
```

예상 출력(대표 예시 — 숫자는 환경마다 다름):

```
[ingest] root=~/.claude/projects
[ingest] scanned 312 files, 47 changed
[ingest] sessions +47, events +18,204, masked 31 secrets
[ingest] done in 142s        # 첫 적재만 20분+, 이후 3분 내외
```

```bash
# ② 요약: 세션을 Haiku로 한 줄 요약 (ANTHROPIC_API_KEY 필요)
.venv/bin/session-archive summarize --mode haiku-only
```

```bash
# ③ 내보내기: 요약 → Obsidian vault 마크다운 (기본 ~/llm-wiki)
.venv/bin/session-archive export-obsidian
```

> 전체를 한 번에: `./scripts/pipeline.sh`
> 매일 04:00 자동 실행은 `bootstrap.sh`가 이미 등록(macOS launchd / Linux cron).

---

## 3. 검색·현황 확인

```bash
# 현황: 적재된 세션/이벤트 수, 프로젝트별 통계
.venv/bin/session-archive stats
```

예상 출력(대표 예시):

```
sessions: 359   events: 1,204,118   masked: 1,042
parse errors: 0
top projects:
  -Users-you-acme-app        128 sessions
  -Users-you-claude-recall     74 sessions
```

```bash
# 검색: 전문검색(FTS5). --project / --since 필터 가능
.venv/bin/session-archive search "STT 정책" --since 30d --limit 5
```

대화 중에는 스킬로도 호출됩니다: `/session-archive-ingest`(적재), `/session-archive-stats`(현황).

---

## 4. Claude가 직접 회상하게 (MCP)

`bootstrap.sh`가 MCP 서버를 `settings.json`에 등록합니다. Claude Code를 재시작하면 Claude가 아래 4개 도구로 과거를 **스스로** 검색합니다.

| 도구 | 용도 |
|---|---|
| `search_history` | 세션 전문검색 |
| `recall_decisions` | 과거에 내린 결정 회상 |
| `recent_checkpoints` | "다음 할 일" 체크포인트 |
| `search_vault` | 다른 노트북에서 내린 결정까지 (크로스머신) |

확인: Claude Code에서 "지난주에 X 어떻게 했었지?" 라고 물어보면 Claude가 `search_history`를 호출해 답합니다.

---

## 5. (선택) 크로스머신 회상

다른 노트북의 결정까지 회상하려면 vault를 git으로 동기화합니다.

```bash
export LLM_WIKI_REMOTE=git@github.com:you/your-vault.git   # 본인 vault repo
.venv/bin/session-archive vault-push     # 이 머신 export를 vault로 송신
.venv/bin/session-archive sync-vault     # 다른 머신 결정 수신(pull)
```

---

## 자주 막히는 곳 (Troubleshooting)

| 증상 | 원인 / 해결 |
|---|---|
| `bootstrap.sh`가 "prerequisites" 에서 멈춤 | 0단계 도구 중 하나가 없음. 출력에 적힌 도구를 설치 후 재실행(멱등) |
| `summarize`가 인증 에러 | `ANTHROPIC_API_KEY` 미설정. `export ANTHROPIC_API_KEY=...` 또는 `~/.env` |
| 세션 로그를 못 찾음 | 로그 위치가 기본과 다름. `--root` 또는 `SESSION_ARCHIVE_ROOT` 지정 |
| MCP 회상이 안 됨 | Claude Code 재시작 안 함. 재시작 후 다시 시도 |
| `~/.claude/settings.json` 권한 가드에 막힘 | 에이전트로 bootstrap 실행함. 터미널에서 직접 실행 |
| 스케줄러 로그 확인 | `~/.claude-archive/launchd-pipeline.log` |

---

## 더 읽기

- 전체 개요·구조: `README.md`
- 전역 규칙: `CLAUDE.md` (+ `docs/`)
- 설계 배경(왜 이렇게 만들었나): `docs/adr/ADR-001-session-log-archive.md`, `docs/adr/ADR-002-rag-llm-wiki-architecture.md`
