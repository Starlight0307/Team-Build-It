"""Ollama 엔진(llama-server) 안정성 실험 — 2026-10-19 Intel Arc 노트북에서 큰 요청에 엔진이 죽은 문제를
다른 PC에서 같은 방법으로 재현·비교하기 위한 스크립트. 프로젝트 폴더에서 실행한다.

  python scripts/ollama_engine_probe.py size 3000 [횟수]     # 순수 글 약 N토큰을 보내 엔진이 버티는지
  python scripts/ollama_engine_probe.py size 6000 3 --cpu    # 그래픽 없이 CPU로만 (메모리 넉넉한 PC에서)
  python scripts/ollama_engine_probe.py lumi "매일 알림 취소해줘"   # 루미가 실제로 보내는 요청의 토큰 수

결과 줄: OK/FAIL, 실제 토큰 수(prompt_eval_count), 걸린 시간. FAIL이면 Windows 이벤트 뷰어 > 응용 프로그램에
llama-server.exe 오류(0xe06d7363)가 같이 남는지 확인한다. 오류 창이 뜨면 [확인]을 눌러야 다음 요청이 진행된다.
"""
import json
import random
import sys
import time

import ollama

sys.path.insert(0, ".")
from settings.config import OLLAMA_MODEL, OLLAMA_NUM_CTX   # noqa: E402

UNIT = "오늘 날씨가 맑고 기온은 십도입니다. "   # llama3.1 기준 약 10토큰


def probe_size(target: int, times: int, cpu: bool):
    opts = {"num_predict": 8, "num_ctx": OLLAMA_NUM_CTX}
    if cpu:
        opts["num_gpu"] = 0
    for _ in range(times):
        # 매번 앞에 다른 숫자를 붙인다 — 같은 글이면 Ollama가 이전 계산을 재사용해 시험이 안 된다
        text = f"[{random.random()}] " + UNIT * (target * 2 // 30)
        t = time.time()
        try:
            r = ollama.chat(model=OLLAMA_MODEL, options=opts,
                            messages=[{"role": "user", "content": text + "\n위 글을 한 단어로 요약해."}])
            print(f"OK   tokens={r['prompt_eval_count']} {'cpu' if cpu else 'gpu'} {time.time() - t:.0f}s", flush=True)
        except Exception as e:
            print(f"FAIL target~{target} {'cpu' if cpu else 'gpu'} {time.time() - t:.0f}s {str(e)[:100]}", flush=True)


def probe_lumi(question: str):
    """AIWorker가 만드는 요청을 가로채(모델 호출 직전) 큰 문맥으로 보내 실제 토큰 수를 잰다."""
    from core import ai_worker
    from core.plugin_manager import load_existing_plugins
    tools, names = [], []
    load_existing_plugins(tools, names)
    captured, real_chat = [], ollama.chat

    def spy(**kw):
        captured.append(kw)
        raise RuntimeError("captured")
    ai_worker.ollama.chat = spy
    w = ai_worker.AIWorker(question, [], tools, None)
    w.response_ready.connect(lambda *_: None)
    w.run()
    if not captured:
        print("모델을 거치지 않는 요청이에요(규칙으로 바로 처리).")
        return
    kw = captured[0]
    r = real_chat(model=kw["model"], messages=kw["messages"], tools=kw.get("tools"),
                  options={"num_predict": 1, "num_ctx": 16384})
    print(f"tools={len(kw.get('tools') or [])} tools_json_chars={len(json.dumps(kw.get('tools') or [], ensure_ascii=False))} "
          f"prompt_tokens={r['prompt_eval_count']} estimate={ai_worker._estimate_prompt_tokens(kw['messages'], kw.get('tools'))}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 3 and sys.argv[1] == "size":
        probe_size(int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 1,
                   "--cpu" in sys.argv)
    elif len(sys.argv) >= 3 and sys.argv[1] == "lumi":
        probe_lumi(sys.argv[2])
    else:
        print(__doc__)
