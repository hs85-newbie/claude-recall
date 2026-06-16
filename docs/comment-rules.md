# 코드 주석 규칙

## 원칙
- "무엇을"이 아니라 "왜를" 남긴다 / 주석 언어: 한국어

## 공개 함수 (JSDoc 필수)
/**
 * 함수가 하는 일 (한 줄)
 * @param 파라미터명 - 설명
 * @returns 반환값 설명
 * @throws {에러타입} 언제 던지는지
 * @example const result = await someFunction('인자');
 */

## 인라인 주석 태그
// WHY: 이유 / // WORKAROUND: 임시 해결책 (개선 시점 명시)
// TODO: [담당자] [YYYY-MM] / // FIXME: [담당자]
// HACK: 임시 우회 (제거 조건 명시) / // REF: [레퍼런스명]

## 금지
- 코드를 그대로 말로 옮기는 주석 / console.log 커밋
