// 카카오 액세스 토큰 갱신. 앱 시크릿은 데스크톱 앱에 넣을 수 없어서 서버에서 처리한다.
// 로그인한 Lumi 사용자만 부를 수 있고(verify_jwt), 토큰을 저장하거나 기록하지 않는다.
Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response(null, { status: 405 });
  const refreshToken = (await req.json().catch(() => ({})))?.refresh_token;
  const clientId = Deno.env.get("KAKAO_REST_API_KEY");
  if (typeof refreshToken !== "string" || !refreshToken || !clientId) return new Response(null, { status: 400 });
  const body = new URLSearchParams({ grant_type: "refresh_token", client_id: clientId, refresh_token: refreshToken });
  const secret = Deno.env.get("KAKAO_CLIENT_SECRET");
  if (secret) body.set("client_secret", secret);
  const resp = await fetch("https://kauth.kakao.com/oauth/token", { method: "POST", body });
  if (!resp.ok) return new Response(null, { status: resp.status === 400 || resp.status === 401 ? 401 : 502 });
  const data = await resp.json();
  return Response.json({
    access_token: data.access_token,
    expires_in: data.expires_in,
    refresh_token: data.refresh_token ?? null,
  });
});
