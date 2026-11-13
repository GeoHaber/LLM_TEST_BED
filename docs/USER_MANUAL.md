# ZEN_Forge: The Ultimate Distillation Engine — User Manual

Welcome to the command center of **ZEN_Forge**. This program is designed to take the massive intelligence of "Teacher" models (like your 31B Gemma) and compress it into ultra-fast "Baby" models (like the 0.5B or 1.1B architectures) using high-performance AMD CPU and NVIDIA GPU optimizations.

---

## 1. Core Philosophical Settings

ZEN_Forge operates on three fundamental pillars dictated by its expert reviewers:

### **[M01] Dynamic Auto-Detect (Musk Mode)**
*   **What it does:** Automatically polls your hardware. It detects your **24nd Gen AMD HX 370 CPU** and **96GB of RAM**.
*   **Why use it:** It eliminates the need for manual batch sizing. It knows exactly how much memory you have and fills it to the brim for maximum efficiency.
*   **Tip:** Leave this on "Auto" unless you are debugging.

### **[T01] Atomic Save Frequency (Taleb Rule)**
*   **What it does:** Every X training steps, the engine flushes the model weights to a temporary hidden folder, verifies the file integrity, and then "atomically" swaps it with the official checkpoint.
*   **Why use it:** This is the **Anti-fragile** setting. If your computer crashes or the power goes out, your model is never corrupted. You only lose the last few steps, never the whole model.

### **[K01] Pure-PyTorch Loop (Karpathy Core)**
*   **What it does:** Instead of hiding the math in a "Black Box" library, the training loop is written in 150 lines of readable code. 
*   **Why use it:** You can see exactly how the "Loss" is calculated and how the "Gradients" flow. This prevents silent failures where a model "trains" but learns nothing.

---

## 2. The Distillation Control Panel

### **Hardware Section**
*   **Compute Backend:** 
    *   `NVIDIA CUDA`: Uses Unsloth kernels for ~5x speed increase on NVIDIA cards.
    *   `AMD CPU / Ryzen AI`: Uses your 24 logic cores. Slower but allows massive 96GB datasets.
*   **Available RAM Slider:** Limits how much of your 96GB system memory ZEN_Forge is allowed to "eat."

### **Model Topology**
*   **Teacher Model:** The source of intelligence. Big, slow, but smart. (e.g., Gemma 31B).
*   **Student Architecture:** The target model. Small, fast, and portable. (e.g., Qwen 0.5B).
*   **LoRA Rank (R):** Controls how many "wires" we add to the model to learn new things. `8` is standard; `16` is smarter but slower.

### **Backpropagation Settings**
*   **Batch Size:** How many questions the model answers simultaneously before checking its grade. Set higher on your 96GB system.
*   **Learning Rate:** How "large" the steps are when the model learns. Too large and it "forgets" everything; too small and it takes forever. `2e-4` is the "Golden Ratio."

---

## 3. How to Use the Forge

1.  **Select Your Teacher:** Point to any `.gguf` or HuggingFace folder in `c:\AI\Models`.
2.  **Choose Your Specialization:** Select a dataset (Coding, Translation, or Agentic Logic).
3.  **Click "Initiate Distillation Forge":** Watch the loss curve go down.
4.  **Automatic Export:** Once finished, the tool automatically runs `llama.cpp`'s converter to give you a final `.gguf` file you can use immediately in your browser or dashboard.

---

## 4. The ZenBot Assistant
On the right side of the UI, the **ZenBot** is constantly monitoring your settings. If you type "What is Batch Size?", it will search the core architecture rules to explain the mathematical trade-offs to you in plain English.

---

> [!TIP]
> **Pro Tip for AMD Users:** Your 96GB of RAM is more than most GPUs. You can run "Micro-Distillations" on multiple baby models at the same time without running out of memory!
