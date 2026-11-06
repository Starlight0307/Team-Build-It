# 서버 설정 (카카오 연결 해제 웹훅, 회원 탈퇴)

코드는 `supabase/`에 있다. 키 값은 직접 입력하고 저장소나 대화에 적지 않는다. 카카오는 로그인 수단으로만 쓴다(캘린더 없음).

1. SQL 실행: Supabase SQL Editor에서 `supabase/migrations/`의 두 파일 실행
2. 함수 비밀 값 등록 (Edge Functions → Secrets): `KAKAO_ADMIN_KEY`(어드민 키), `KAKAO_APP_ID`(앱 ID 숫자)
3. 함수 배포: `supabase functions deploy kakao-unlink --no-verify-jwt`, `supabase functions deploy delete-account --no-verify-jwt`
4. 카카오 콘솔 [앱] → [웹훅] → [연결 해제 웹훅]: `https://<프로젝트>.supabase.co/functions/v1/kakao-unlink`

## 동작

- 웹훅은 카카오 로그인 연결만 지운다. 카카오로만 가입한 계정은 로그인 상태를 끊고, 계정 삭제는 하지 않는다.
- 탈퇴 함수는 요청 내용이 아니라 로그인 토큰으로 본인을 확인해 그 계정만 지운다.
