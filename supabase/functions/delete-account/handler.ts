// 회원 탈퇴: 요청한 본인의 계정만 지운다. 지울 대상은 요청 내용이 아니라 로그인 토큰으로 정한다.
export type Env = { userIdOf: (token: string) => Promise<string | null>; deleteUser: (id: string) => Promise<void> };

export async function handle(req: Request, env: Env): Promise<Response> {
  if (req.method !== "POST") return new Response(null, { status: 405 });
  const token = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  const id = token ? await env.userIdOf(token).catch(() => null) : null;
  if (!id) return new Response(null, { status: 401 });
  try {
    await env.deleteUser(id);
    return new Response(null, { status: 200 });
  } catch (e) {
    console.error("계정 삭제 실패:", (e as Error).name);
    return new Response(null, { status: 500 });
  }
}
