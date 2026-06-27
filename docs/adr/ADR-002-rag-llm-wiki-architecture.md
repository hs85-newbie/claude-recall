# ADR-002: RAG/LLM-wiki 크로스머신 아키텍처

- **상태**: Accepted
- **결정일**: 2026-06-15
- **결정권자**: you
- **선행 ADR**: [ADR-001](ADR-001-session-log-archive.md) (세션 로그 아카이브 — L1)
- **관련 커밋**: `a1d80f1`(키 로더), `e23e0c3`(summarize 수정), `e78c40f`(체크포인트 ingest)

---

## 1. 배경 (Context)

session-archive(ADR-001)가 세션 로그를 적재·요약·Obsidian export까지 동작하게 된 뒤, **여러 인프라(맥/윈도우 등)에서 동일한 RAG/LLM-wiki를 쓰고 싶다**는 요구가 생겼다. 초기 외부 인프라(Railway 기반) 종료로 **"중앙 서버·죽은 인프라 0, 휴대성"** 기조가 확립된 상태.

핵심 질문 4가지를 순서대로 결정했다:
1. 통합 저장·동기화를 무엇으로? (git vs Railway/Vercel)
2. 머신 간 vault를 통합할 것인가, 격리할 것인가?
3. 적층 데이터는 불변(read-only)인가? 누가 언제 수정하나?
4. 다른 PC에서 작업을 "이어서" 할 수 있나?

---

## 2. 결정 (Decisions)

### D1. 저장·동기화 = **git repo** (개인용 기준)

| 기준 | git repo | Railway(상시) | Vercel(서버리스) |
|---|---|---|---|
| 비용/운영 | 무료/거의 0 | 월정액·운영 부담 | 사용량·DB별도 |
| 벤더 종속 | 없음 | 있음(걷어낸 그것) | 있음 |
| 오프라인 | 완전 | 불가 | 불가 |
| 시맨틱 RAG | 로컬 임베딩으로 가능 | pgvector | 외부 vector DB |

- **개인용 = git repo가 우위**(무료·무운영·휴대·프라이버시). RAG는 compute가 있는 로컬(Claude API/LM Studio)에서 실행.
- **호스팅은 "구체적 필요" 발생 시에만 얇게**: 폰/무compute 기기 질의 → Vercel(scale-to-zero), 상시 자율 에이전트 → Railway. 단 **git을 진실 소스로 두고 호스팅은 읽기 계층**.

### D2. vault = **단일 git repo + 머신 네임스페이스** (격리+통합)

```
llm-wiki (단일 repo)
├── sessions/<machine>/      ← 각 머신은 자기 네임스페이스만 write
├── decisions/<machine>/
├── checkpoints/<machine>/   ← 체크포인트(다음 할 일/인계)
└── git pull → 모든 머신이 전체 union을 read
```

- **쓰기는 머신별 격리(충돌 0), 읽기는 전체 통합(인사이트 극대화).**
- 머신별 개별 vault(완전 격리)보다 이 방식이 우위 — 인사이트는 **통합 코퍼스**에서 나오므로.
- 윈도우는 WSL(파이프라인이 bash+Python), 스케줄러만 cron/Task Scheduler.

### D3. 불변식 = **"네임스페이스별 단일 작성자"** (read-only 아님)

적층 데이터는 불변이 아니다. **수정되는 정상 케이스**:

| 케이스 | 무엇이 바뀌나 | 작성자 |
|---|---|---|
| 재요약(모델 업글·품질 재시도·버그픽스) | 세션 노트 덮어씀(`ON CONFLICT DO UPDATE`) | 로컬 파이프라인 |
| 마스킹/레드액션 수정 | 노트 재작성 | 로컬 파이프라인 |
| export 포맷 변경 | 전체 재생성 | 로컬 파이프라인 |
| 사람 큐레이션 | 손수 교정 | 사람(로컬) |
| LLM 종합 산출물 | `synthesis/` 생성·갱신 | RAG/LLM 계층 |

- 진짜 불변식: **"한 파일을 두 주체가 동시에 안 건드린다"**(단일 작성자).
- 같은 작성자의 **멱등 재생성(재요약 덮어쓰기)은 안전**.
- 호스팅이 생성물을 쓸 땐 **`synthesis/` 별도 네임스페이스 + PR/파생스토어 경유**(서버리스→git 직접 push는 취약). source(sessions/decisions/checkpoints)는 **호스팅 read-only**.

### D4. 크로스머신 연속성 = **체크포인트/복원 모델** (라이브 재개 아님)

| 층위 | 이어짐 | 운반 |
|---|---|---|
| 지식·맥락(결정·요약) | ✅ | wiki git pull (마스킹 아카이브) |
| 코드 작업물(WIP) | ✅ | 프로젝트 repo git push/pull |
| 라이브 Claude 대화 | ❌ | 로컬 raw jsonl만, 동기화 안 함 |

- "이어서 = 프로세스 재개"가 아니라 **"스냅샷 읽어 복원"**.
- **왜**(decisions)는 wiki가, **다음 할 일**(next steps)은 **체크포인트**가 더 정확 → 체크포인트를 RAG에 적재(아래 §3).
- 단기 인계 = `/context-save`, 장기 회상 = wiki/RAG의 2단 구조.

---

## 3. 구현 (이번 회차)

- **체크포인트 ingest**(`e78c40f`): `~/.gstack/projects/*/checkpoints/*.md`(229개)를 `checkpoints` 테이블+FTS로 증분 적재. `session-archive search`에 통합 표시, vault `checkpoints/<machine>/`로 미러. summarize 불필요(이미 간결).
- **summarize 근본 수정**(`e23e0c3`): user 턴 캡+하드백스톱(400 방지), 구조화 출력+max_tokens 4096(json_parse_failed 방지). E2E ok=247/247.
- **키 로더**(`a1d80f1`): `~/.env` + 레포 루트 `.env` 양쪽 로드.

## 4. RAG 활용 단계

| 단계 | 방법 | 상태 |
|---|---|---|
| 검색(retrieval) | `session-archive search`(FTS5, 세션+체크포인트 통합) | ✅ 가능 |
| 시맨틱 | 로컬 임베딩(sqlite-vec 등) — 서버 불필요 | 후보 |
| LLM 회상 | Claude용 MCP(과거 결정 자동 호출) 또는 `ask` 서브커맨드 | ✅ search_vault MCP |
| 오답노트 회상 | gstack `learnings.jsonl` → `export-learnings` → vault `learnings/<machine>/`, `search_vault`(kind=learnings)로 grep 회상 | ✅ 가능 |
| 사람 탐색 | Obsidian(`~/llm-wiki`, 그래프/태그) | ✅ 가능 |

## 5. 결과 (Consequences)

**긍정**: 무비용·무운영·휴대; 머신 격리+통합 동시; 충돌 없는 단일 작성자 모델; 체크포인트로 "다음 할 일" 인계 가능.

**유의/한계**:
- wiki는 **04:00 파이프라인 지연** — 당일 인계는 코드 repo(git)가 운반, 맥락은 수동 `pipeline.sh`+push로 보완.
- 공유 vault에 올라가는 건 **마스킹 아카이브**(시크릿 복원 불가, 설계상).
- 라이브 대화 재개 불가(체크포인트로 대체).

## 6. 향후 트리거 (재결정 조건)

| 트리거 | 전환 |
|---|---|
| 다수 사용자가 **읽기만** | git 소스 + Vercel read-only(synthesis만 write) |
| 다수 사용자가 **쓰기**(각자 기여) | 진실 소스를 git→**DB(Postgres 등)** 승격 (이때만) |
| 폰/무compute 질의 | 얇은 호스팅 read API |
| 상시 자율 에이전트 | Railway 상주 워커 |

→ 현재는 모두 **해당 없음**. git+로컬 RAG가 정답.
