# API 설계 표준

## 명세 우선 (Spec-First)
- OpenAPI 3.0 스펙 먼저 작성 → 코드 생성
- 스펙 파일 위치: docs/api/openapi.yaml

## URL 구조
- 버전: /api/v1/, /api/v2/ (하위 호환 의무)
- 리소스: 복수형 명사 (/api/v1/users, /api/v1/orders)
- 동작: HTTP 메서드로 표현 (GET/POST/PUT/PATCH/DELETE)

## 응답 형식 통일
{
  "data": {},          // 성공 시 페이로드
  "error": null,       // 실패 시 { code, message, details }
  "meta": {            // 페이지네이션, 타임스탬프 등
    "timestamp": "ISO8601",
    "version": "v1"
  }
}

## 페이지네이션
- 대용량(1만+ 건): cursor-based
- 소용량: offset-based (page, limit, total)

## API 버전 전환 정책
- 구 버전: 6개월 Deprecation Notice 후 종료
- 전환 기간 중 양 버전 동시 지원
