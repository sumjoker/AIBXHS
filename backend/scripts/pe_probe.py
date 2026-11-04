"""PE 存活率探针：同图、同 PE 参数、只换原始提示词，取回 PE 增强文本。

- 不跑采样（无 KSampler），只执行「读图 → PE → 文本输出」三个节点，单次约数秒。
- 参考图与生产一致：先上传到 ComfyUI /upload/image，再经 532 缩放到同像素尺度。
- seed 固定 666、输出中文、关闭思考，与生产 565 节点参数逐项对齐（仅「生成后自动卸载模型」设为 false 以便连续跑）。

用法：
    py -3.13 backend/scripts/pe_probe.py                 # 跑全部变体
    py -3.13 backend/scripts/pe_probe.py V2 V3           # 只跑指定变体
结果：backend/scripts/pe_probe_results.json + 控制台摘要
"""
import copy
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

import requests  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from config import get_settings  # noqa: E402
from services.ai_image_service import _api  # noqa: E402

WORKFLOW = os.path.join(BACKEND, "workflows", "pe_probe_only.json")
VARIANTS = os.path.join(BACKEND, "scripts", "pe_probe_variants.json")
OUT = os.path.join(BACKEND, "scripts", "pe_probe_results.json")
TASK_ID = 65
POLL_INTERVAL = 2
TIMEOUT_PER_VARIANT = 300


def load_product_info() -> tuple:
    """从任务 65 取真实 product_info 与参考图 URL（避免手抄出错）"""
    eng = create_engine(get_settings().DATABASE_URL)
    with eng.connect() as c:
        row = c.execute(
            text("SELECT product_info, images FROM ai_image_tasks WHERE id = :i"), {"i": TASK_ID}
        ).mappings().first()
    return row["product_info"], json.loads(row["images"])[0]


def fetch_image(url: str) -> bytes:
    local = os.path.join(os.path.dirname(BACKEND), "_ap65_ref.jpg")
    if os.path.exists(local):
        with open(local, "rb") as f:
            return f.read()
    return requests.get(url, timeout=60).content


def upload_reference(content: bytes) -> str:
    name = f"pe-probe-ref-{int(time.time())}.jpg"
    resp = _api("POST", "/upload/image", files={"image": (name, content)},
                data={"overwrite": "true"}, timeout=120)
    return resp.json()["name"]


def extract_text(outputs: dict) -> tuple:
    """从 /history outputs 里捞出 PE 文本；返回 (text, 命中节点, 原始 outputs)"""
    for node_id, out in (outputs or {}).items():
        if not isinstance(out, dict):
            continue
        for key in ("text", "string", "STRING", "ui"):
            val = out.get(key)
            if isinstance(val, str) and val.strip():
                return val, f"{node_id}.{key}", outputs
            if isinstance(val, list) and val and isinstance(val[0], str) and val[0].strip():
                return val[0], f"{node_id}.{key}[0]", outputs
        inner = out.get("text")
        if isinstance(inner, dict):
            for k2 in ("text", "string"):
                v2 = inner.get(k2)
                if isinstance(v2, str) and v2.strip():
                    return v2, f"{node_id}.text.{k2}", outputs
    return "", "", outputs


def run_variant(wf_template: dict, image_name: str, prompt: str, seed: int = 666) -> dict:
    wf = copy.deepcopy(wf_template)
    wf["475"]["inputs"]["image"] = image_name
    wf["565"]["inputs"]["输入提示词"] = prompt
    wf["565"]["inputs"]["seed"] = seed

    t0 = time.time()
    resp = _api("POST", "/prompt", json={"prompt": wf}, timeout=60)
    pid = resp.json()["prompt_id"]

    while time.time() - t0 < TIMEOUT_PER_VARIANT:
        hist = _api("GET", f"/history/{pid}", timeout=30).json()
        entry = hist.get(pid)
        if entry:
            status = (entry.get("status") or {}).get("status_str")
            if status == "error" or (entry.get("status") or {}).get("completed") is False:
                return {"ok": False, "error": json.dumps(entry.get("status"), ensure_ascii=False)[:800],
                        "seconds": round(time.time() - t0, 1)}
            text, hit, raw = extract_text(entry.get("outputs") or {})
            return {"ok": True, "prompt_id": pid, "seconds": round(time.time() - t0, 1),
                    "text": text, "hit": hit, "raw_outputs": raw}
        time.sleep(POLL_INTERVAL)
    return {"ok": False, "error": "timeout", "seconds": round(time.time() - t0, 1)}


def main() -> None:
    wanted = [a.strip().upper() for a in sys.argv[1:] if not a.startswith("-")]
    product_info, ref_url = load_product_info()
    template = json.load(open(WORKFLOW, encoding="utf-8"))
    spec = json.load(open(VARIANTS, encoding="utf-8"))

    variants = [v for v in spec["variants"] if not wanted or v["id"].upper() in wanted]
    print(f"变体 {len(variants)} 个；参考图 {ref_url[:80]}")

    image_name = upload_reference(fetch_image(ref_url))
    print(f"参考图已上传：{image_name}\n")

    results = []
    for v in variants:
        prompt = v["text"].replace("{product_info}", product_info)
        r = run_variant(template, image_name, prompt)
        r.update({"id": v["id"], "name": v["name"], "tests": v["tests"],
                  "raw_prompt": prompt, "raw_len": len(prompt)})
        if r.get("ok"):
            r["pe_len"] = len(r.get("text") or "")
            print(f"[{v['id']}] {v['name']}")
            print(f"    原文 {len(prompt)} 字 → PE {r['pe_len']} 字 / {r['seconds']}s / 命中 {r['hit']}")
            print(f"    PE: {(r['text'] or '')[:160]}...\n")
        else:
            print(f"[{v['id']}] 失败：{r.get('error')}\n")
        results.append(r)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"已写：{OUT}")
    if any(r.get("ok") and not r.get("text") for r in results):
        print("注意：有变体跑通但取不到文本，检查 raw_outputs 字段定位输出节点")


if __name__ == "__main__":
    main()
