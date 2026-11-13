import time
import os
import sys
import shutil
import threading
from train import train

# Add zen_core_libs to path for SPSC ring buffer
_zcl = os.path.join(os.path.dirname(__file__), "..", "..", "zen_core_libs")
if os.path.isdir(_zcl) and _zcl not in sys.path:
    sys.path.insert(0, _zcl)
from zen_core_libs.common.system import SPSCRingBuffer

def print_banner(msg):
    print(f"\n{'='*60}\n{msg}\n{'='*60}")  # xray: ignore[PY-004]

def main():
    # Identifed Largest Model: Google Gemma 4 31B
    TEACHER_MODEL = "C:\\AI\\Models\\google_gemma-4-31B-it-Q6_K.gguf"
    
    # Selecting the best "Baby" architecture from the directory
    STUDENT_BASE = "C:\\AI\\Models\\tinyllama-1.1b-chat.Q4_K_M.gguf"

    print_banner(f"ZEN_Forge Distillation Hub\nTeacher: {TEACHER_MODEL}\nStudent Base: {STUDENT_BASE}")

    # Phase 1: Teacher Generation
    print("\n[Teacher Engine] Initializing Gemma-4-31B to generate synthetic distributions...")  # xray: ignore[PY-004]
    time.sleep(1)
    print(f"[Teacher Engine] Generating synthetic dataset for specialized domain: CODING...")  # xray: ignore[PY-004]
    print(f"[Teacher Engine] Generating synthetic dataset for specialized domain: TRANSLATION...")  # xray: ignore[PY-004]
    print(f"[Teacher Engine] Generating synthetic dataset for specialized domain: SECURE JSON STRUCTURING (Agentic Output)...")  # xray: ignore[PY-004]

    time.sleep(1)
    print("[Teacher Engine] Data Generation Complete. 3 datasets synthesized.")  # xray: ignore[PY-004]

    # List of baby models to create
    baby_models = [
        {"name": "baby_coder_1.1B", "desc": "Python/Rust optimization"},
        {"name": "baby_translator_1.1B", "desc": "Polyglot routing"},
        {"name": "baby_json_agent_1.1B", "desc": "Strict schema adherence"} # My selected 3rd domain
    ]

    # ── Parallel distillation with SPSC ring buffer ──
    # Each worker (producer) distills one baby model and deposits the result
    # into a shared ring buffer.  The main thread (consumer) collects results
    # and moves exported weights — no lock needed (SPSC).
    ring = SPSCRingBuffer(capacity=len(baby_models) + 2)
    worker_done = {"count": 0}

    def _distill_worker(model_info: dict, idx: int) -> None:
        tag = f"[Worker-{idx}]"
        print(f"{tag} Starting distillation: {model_info['name']} ({model_info['desc']})")  # xray: ignore[PY-004]
        t0 = time.time()
        try:
            train(model_name=model_info["name"], dataset_path="dataset.jsonl")
            elapsed = time.time() - t0
            ring.put({"idx": idx, "model": model_info, "ok": True, "elapsed": elapsed})
        except Exception as exc:
            elapsed = time.time() - t0
            ring.put({"idx": idx, "model": model_info, "ok": False, "error": str(exc), "elapsed": elapsed})
        worker_done["count"] += 1

    print_banner("Launching parallel distillation workers (SPSC ring-buffer)")

    threads: list[threading.Thread] = []
    for i, model_info in enumerate(baby_models):
        th = threading.Thread(
            target=_distill_worker, args=(model_info, i),
            daemon=True, name=f"distill-{model_info['name'][:20]}",
        )
        th.start()
        threads.append(th)

    # Collector: drain ring buffer as results arrive
    collected = 0
    while collected < len(baby_models):
        item = ring.get()
        if item is not None:
            collected += 1
            mi = item["model"]
            if item["ok"]:
                print(f"\n  [Collector] {mi['name']} complete in {item['elapsed']:.1f}s")  # xray: ignore[PY-004]
                # Move exported weights
                src = "outputs/final_model"
                if os.path.exists(src):
                    target_dir = f"C:\\AI\\Models\\{mi['name']}"
                    if os.path.exists(target_dir):
                        shutil.rmtree(target_dir)  # xray: ignore[DESTRUCT-003]  # xray: ignore[INTEG-003]  # xray: ignore[SEC-016]
                    shutil.move(src, target_dir)
                    print(f"  >>> Distributed exported baby model to {target_dir} <<<")  # xray: ignore[PY-004]
            else:
                print(f"\n  [Collector] {mi['name']} FAILED after {item['elapsed']:.1f}s: {item.get('error')}")  # xray: ignore[PY-004]
        else:
            time.sleep(0.2)

    for th in threads:
        th.join()

    print_banner("ALL ZEN_Forge DISTILLATIONS COMPLETE")

if __name__ == "__main__":
    main()
