"""PE 探针：探查 ComfyUI 可用的文本输出节点与 PE 节点的输入/输出契约。

只读：仅调用 /object_info 与 /system_stats，不提交任何 prompt、不占用 GPU。
用法：py -3.13 backend/scripts/probe_pe_nodes.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from services.ai_image_service import _api  # noqa: E402


def show(node: str) -> None:
    try:
        info = _api("GET", f"/object_info/{node}", timeout=20).json()
    except Exception as e:  # noqa: BLE001
        print(f"[缺失] {node}: {e}")
        return
    spec = (info or {}).get(node) or {}
    if not spec:
        print(f"[缺失] {node}: 空定义")
        return
    print(f"[存在] {node}")
    print("  category:", spec.get("category"))
    print("  output_node:", spec.get("output_node"))
    print("  output:", spec.get("output"), spec.get("output_name"))
    req = ((spec.get("input") or {}).get("required") or {})
    opt = ((spec.get("input") or {}).get("optional") or {})
    print("  required:", list(req.keys()))
    print("  optional:", list(opt.keys()))
    # 关键输入的类型/约束
    for key in ("输入提示词", "图片", "text", "input_text", "use_edited_text", "输出语言", "seed", "最大生成token"):
        if key in req or key in opt:
            v = req.get(key) or opt.get(key)
            print(f"    - {key}: {json.dumps(v, ensure_ascii=False)[:220]}")


for n in [
    "TE_Qwen_Image_2_1_Prompt_Enhancer",
    "TE_text_display",
    "PreviewAny",
    "ShowText|pysssss",
    "ShowText",
    "Text Preview",
]:
    show(n)

print("\n=== 系统信息 ===")
try:
    st = _api("GET", "/system_stats", timeout=15).json()
    for d in st.get("devices", []):
        print(f"  {d.get('name')} vram_total={d.get('vram_total')} vram_free={d.get('vram_free')}")
    print("  comfyui_version:", (st.get("system") or {}).get("comfyui_version"))
except Exception as e:  # noqa: BLE001
    print("  取不到:", e)

print("\n=== 队列 ===")
try:
    q = _api("GET", "/queue", timeout=15).json()
    print("  running:", len(q.get("queue_running") or []), " pending:", len(q.get("queue_pending") or []))
except Exception as e:  # noqa: BLE001
    print("  取不到:", e)
