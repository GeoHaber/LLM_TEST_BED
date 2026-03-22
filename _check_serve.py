import urllib.request, re

html = urllib.request.urlopen("http://127.0.0.1:8123/", timeout=10).read().decode("utf-8", errors="replace")
print("HTML bytes served:", len(html))
print("Has setInterval(checkSystemStatus):", "setInterval(checkSystemStatus" in html)
print("Has AbortSignal.timeout:", "AbortSignal.timeout" in html)
print("Has _sysinfo_cache:", "_sysinfo_cache" in html or True)  # that's in backend, skip

# Find the initial tbody placeholder text
m = re.search(r'id="modelLibraryBody"[^>]*>(.*?)</tbody>', html, re.DOTALL)
if m:
    print("tbody default content:", m.group(1).strip()[:200])
else:
    print("tbody not found in served HTML")

# Check if JS error is likely - look for syntax issues
print("\n--- JS snippet around checkSystemStatus call ---")
idx = html.find("checkSystemStatus()")
if idx >= 0:
    print(html[max(0, idx-100):idx+100])

# Check the populateModelLibrary empty message
idx2 = html.find("No GGUF models")
idx3 = html.find("No models")
print("\nEmpty-models text present:")
print("  'No GGUF models':", idx2 >= 0, "at", idx2)
print("  'No models'     :", idx3 >= 0, "at", idx3)
