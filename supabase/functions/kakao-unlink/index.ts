import { handle } from "./handler.ts";

async function unlink(kakaoUserId: string): Promise<void> {
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  const resp = await fetch(`${Deno.env.get("SUPABASE_URL")}/rest/v1/rpc/kakao_unlink`, {
    method: "POST",
    headers: { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ kakao_user_id: kakaoUserId }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
}

Deno.serve((req) =>
  handle(req, { adminKey: Deno.env.get("KAKAO_ADMIN_KEY"), appId: Deno.env.get("KAKAO_APP_ID"), unlink },
    // deno-lint-ignore no-explicit-any
    (work) => (globalThis as any).EdgeRuntime?.waitUntil(work))
);
