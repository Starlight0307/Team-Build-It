import { handle } from "./handler.ts";

function check(ok: boolean, what: string) {
  if (!ok) throw new Error(what);
}

const URL_OK = "https://x/?app_id=7&user_id=123&referrer_type=UNLINK_FROM_APPS";
const AUTH = { authorization: "KakaoAK secret" };

async function run(url: string, headers: Record<string, string>, env = {}, init: RequestInit = {}) {
  const calls: string[] = [];
  const works: Promise<void>[] = [];
  const res = await handle(new Request(url, { headers, ...init }), {
    adminKey: "secret", appId: "7", unlink: (id) => (calls.push(id), Promise.resolve()), ...env,
  }, (w) => works.push(w));
  await Promise.all(works);
  return { status: res.status, calls };
}

Deno.test("올바른 웹훅은 연결을 끊는다", async () => {
  const r = await run(URL_OK, AUTH);
  check(r.status === 200 && r.calls[0] === "123", JSON.stringify(r));
});

Deno.test("POST 폼도 받는다", async () => {
  const r = await run("https://x/", AUTH, {}, { method: "POST", body: "app_id=7&user_id=9&referrer_type=ACCOUNT_DELETE" });
  check(r.status === 200 && r.calls[0] === "9", JSON.stringify(r));
});

Deno.test("키가 틀리면 아무것도 지우지 않는다", async () => {
  const r = await run(URL_OK, { authorization: "KakaoAK wrong" });
  check(r.status === 401 && r.calls.length === 0, JSON.stringify(r));
});

Deno.test("서버에 키가 설정되지 않았으면 거부한다", async () => {
  const r = await run(URL_OK, { authorization: "KakaoAK " }, { adminKey: "" });
  check(r.status === 401 && r.calls.length === 0, JSON.stringify(r));
});

Deno.test("다른 앱의 웹훅이나 이상한 값은 200을 주되 지우지 않는다", async () => {
  for (const url of ["https://x/?app_id=8&user_id=1&referrer_type=UNLINK_FROM_APPS",
    "https://x/?app_id=7&user_id=1;drop&referrer_type=UNLINK_FROM_APPS", "https://x/?app_id=7&user_id=1&referrer_type=X"]) {
    const r = await run(url, AUTH);
    check(r.status === 200 && r.calls.length === 0, url);
  }
});

Deno.test("지우기가 실패해도 200으로 답한다", async () => {
  const r = await run(URL_OK, AUTH, { unlink: () => Promise.reject(new Error("db")) });
  check(r.status === 200, JSON.stringify(r));
});
