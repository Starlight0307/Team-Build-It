import { handle } from "./handler.ts";

const url = Deno.env.get("SUPABASE_URL") ?? "";
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

async function userIdOf(token: string): Promise<string | null> {
  const resp = await fetch(`${url}/auth/v1/user`, { headers: { apikey: serviceKey, Authorization: `Bearer ${token}` } });
  return resp.ok ? (await resp.json()).id ?? null : null;
}

async function deleteUser(id: string): Promise<void> {
  const resp = await fetch(`${url}/rest/v1/rpc/delete_user_account`, {
    method: "POST",
    headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ target: id }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
}

Deno.serve((req) => handle(req, { userIdOf, deleteUser }));
