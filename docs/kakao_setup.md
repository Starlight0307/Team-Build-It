# 서버 설정 (카카오 웹훅, 톡캘린더, 회원 탈퇴)

코드는 `supabase/`에 있다. 키 값은 직접 입력하고 저장소나 대화에 적지 않는다.

1. SQL 실행: Supabase SQL Editor에서 `supabase/migrations/`의 두 파일(`20261106_kakao_unlink.sql`, `20261106_delete_account.sql`) 실행
2. 함수 비밀 값 등록 (Edge Functions → Secrets)
   - `KAKAO_ADMIN_KEY`: 카카오 어드민 키
   - `KAKAO_APP_ID`: 카카오 앱 ID (숫자)
   - `KAKAO_REST_API_KEY`: 카카오 REST API 키
   - `KAKAO_CLIENT_SECRET`: 카카오 클라이언트 시크릿 (쓰는 경우)
3. 함수 배포: `supabase functions deploy kakao-unlink --no-verify-jwt`, `supabase functions deploy kakao-token --no-verify-jwt`, `supabase functions deploy delete-account --no-verify-jwt`
4. 카카오 콘솔 [앱] → [웹훅] → [연결 해제 웹훅]: `https://<프로젝트>.supabase.co/functions/v1/kakao-unlink`
5. 공개 전 카카오에 톡캘린더 추가 기능 신청. 그 전에는 앱 멤버만 호출할 수 있다.

## 동작

- 웹훅은 카카오 로그인 연결만 지운다. 카카오로만 가입한 계정은 로그인 상태를 끊고, 계정 삭제는 하지 않는다.
- 토큰 갱신 함수는 토큰을 저장하거나 기록하지 않는다.
