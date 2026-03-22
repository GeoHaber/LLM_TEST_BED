import urllib.request, json, re

data = json.load(urllib.request.urlopen("http://127.0.0.1:8123/__system-info", timeout=20))

print("=== SYSTEM ===")
print("  CPU  :", data.get("cpu_name"))
print("  RAM  :", data.get("memory_gb"), "GB")
gpus = data.get("gpus", [])
for g in gpus:
    print("  GPU  :", g.get("name"), g.get("vram_gb", 0), "GB ->", g.get("backend"))
ver = data.get("llama_cpp_version") or "not installed"
print("  llama:", ver)
print("  Model count:", data.get("model_count"))
print()
print("=== MODEL CARDS ===")
for m in sorted(data.get("models", []), key=lambda x: x.get("name", "")):
    name = m.get("name", "")
    q = re.search(r"[Qq]\d[_KMBSkmbs]+", name)
    quant = q.group(0) if q else "---"
    sz = m.get("size_gb", 0.0)
    print(f"  {name[:60]:<60}  {sz:5.1f} GB  {quant}")
print()
print("=== RESULT ===")
mc = data.get("model_count", 0)
if mc > 0 and gpus:
    print(f"OK: {mc} models, {len(gpus)} GPU(s) detected -> UI should show model library correctly")
elif mc > 0:
    print(f"OK: {mc} models found, no GPU detected (CPU-only mode)")
else:
    print("WARN: 0 models returned - check C:\\AI\\Models path")
