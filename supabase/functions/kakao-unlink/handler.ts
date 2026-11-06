// 카카오 연결 해제 웹훅. 문서: 어드민 키로 인증, 3초 안에 200 응답, 오류가 나도 200.
const REFERRERS = ["ACCOUNT_DELETE", "FORCED_ACCOUNT_DELETE", "UNLINK_FROM_ADMIN", "UNLINK_FROM_APPS", "INCOMPLETE_SIGN_UP"];

export type Env = { adminKey?: string; appId?: string; unlink: (kakaoUserId: string) => Promise<void> };

function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function readParams(req: Request): Promise<URLSearchParams> {
  const params = new URL(req.url).searchParams;
  if (req.method === "POST") {
    new URLSearchParams(await req.text()).forEach((value, key) => params.set(key, value));
  }
  return params;
}

export async function handle(req: Request, env: Env, background: (work: Promise<void>) => void): Promise<Response> {
  if (req.method !== "GET" && req.method !== "POST") return new Response(null, { status: 405 });
  // 키가 설정되지 않았으면 누구의 요청도 믿지 않는다
  if (!env.adminKey || !env.appId || !sameText(req.headers.get("authorization") ?? "", `KakaoAK ${env.adminKey}`)) {
    return new Response(null, { status: 401 });
  }
  const params = await readParams(req);
  const userId = params.get("user_id") ?? "";
  if (params.get("app_id") === env.appId && /^\d+$/.test(userId) && REFERRERS.includes(params.get("referrer_type") ?? "")) {
    background(env.unlink(userId).catch((e) => console.error("kakao_unlink 실패:", (e as Error).name)));
  }
  return new Response(null, { status: 200 });
}
