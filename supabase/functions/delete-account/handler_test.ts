import { type Env, handle } from "./handler.ts";

function check(ok: boolean, what: string) {
  if (!ok) throw new Error(what);
}

async function run(init: RequestInit, env: Partial<Env> = {}) {
  const deleted: string[] = [];
  const res = await handle(new Request("https://x/", init), {
    userIdOf: (t) => Promise.resolve(t === "good" ? "user-1" : null),
    deleteUser: (id) => (deleted.push(id), Promise.resolve()),
    ...env,
  });
  return { status: res.status, deleted };
}

const AUTH = { authorization: "Bearer good" };

Deno.test("본인 토큰이면 그 계정을 지운다", async () => {
  const r = await run({ method: "POST", headers: AUTH });
  check(r.status === 200 && r.deleted[0] === "user-1", JSON.stringify(r));
});

Deno.test("요청 내용으로 다른 사람을 지정할 수 없다", async () => {
  const r = await run({ method: "POST", headers: AUTH, body: JSON.stringify({ id: "user-2", target: "user-2" }) });
  check(r.deleted.length === 1 && r.deleted[0] === "user-1", JSON.stringify(r));
});

Deno.test("토큰이 없거나 틀리면 지우지 않는다", async () => {
  for (const headers of [{}, { authorization: "Bearer bad" }] as Record<string, string>[]) {
    const r = await run({ method: "POST", headers });
    check(r.status === 401 && r.deleted.length === 0, JSON.stringify(r));
  }
});

Deno.test("POST가 아니면 거부한다", async () => {
  const r = await run({ method: "GET", headers: AUTH });
  check(r.status === 405 && r.deleted.length === 0, JSON.stringify(r));
});

Deno.test("삭제가 실패하면 성공이라고 답하지 않는다", async () => {
  const r = await run({ method: "POST", headers: AUTH }, { deleteUser: () => Promise.reject(new Error("db")) });
  check(r.status === 500, JSON.stringify(r));
});
