# UI/UX 디자인 가이드

## DESIGN.md 방식 (우선 적용)

신규 프로젝트 시작 시 아래 순서로 진행:

1. `~/awesome-design-md/design-md/` 에서 유사 서비스 DESIGN.md 선택
   - B2B SaaS → Linear, Notion, Airtable
   - 개발자 도구 → Vercel, Supabase, Sentry
   - 금융/핀테크 → Stripe, Wise, Revolut
   - AI 서비스 → Claude, Mistral, Cohere

2. 선택한 DESIGN.md를 프로젝트 루트에 복사
   cp ~/awesome-design-md/design-md/[선택]/DESIGN.md ./DESIGN.md

3. CLAUDE.md에 한 줄 추가:
   @DESIGN.md (UI 구현 시 이 파일의 디자인 시스템 적용)

→ Claude가 UI 작업 시 색상·타이포그래피·컴포넌트·레이아웃 자동 일관 적용

## 레퍼런스 라이브러리 (구현 패턴)

| 레퍼런스 | 활용 목적 |
|---|---|
| Framer Motion | 애니메이션 · 전환 효과 구현 패턴 |
| UI UX Pro Max | 컴포넌트 디자인 · 인터랙션 패턴 |
| 21st.dev | 현대적 UI 컴포넌트 예시 · 코드 레퍼런스 |
| Google AI Design Stitch | AI 기반 인터페이스 · 제너레이티브 UI |
| Frontend Design | 프론트엔드 디자인 원칙 · 레이아웃 시스템 |

## 적용 원칙
- DESIGN.md 없는 경우에만 위 5가지 레퍼런스 참조
- 애니메이션: Framer Motion 패턴 우선
- 새 패턴 도입 시 // REF: [레퍼런스명] 주석 필수
- a11y: WCAG 2.1 AA 기준 (색상 대비, 키보드 탐색)
