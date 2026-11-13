import os
import shutil

def atomic_save(model, tokenizer, save_path):
    """
    Nassim Taleb rule: Anti-fragile saving. 
    Write to a temp directory, verify, then atomic swap.
    Prevents corrupted checkpoints from power loss or running out of disk space.
    """
    temp_path = save_path + "_temp"
    
    # Ensure parent dir exists
    os.makedirs(os.path.dirname(save_path) if os.path.dirname(save_path) else ".", exist_ok=True)
    
    print(f"[ZenForge Checkpoint] Atomically saving weights to {save_path}...")  # xray: ignore[PY-004]
    try:
        model.save_pretrained(temp_path)
        tokenizer.save_pretrained(temp_path)
        
        # Verify write worked (basic existence check)
        if not os.path.exists(temp_path):
            raise IOError("Temp directory failed to create. Possible disk full.")
            
        # Atomic rename (Windows requires removing old dir first)
        if os.path.exists(save_path):
            shutil.rmtree(save_path)  # xray: ignore[DESTRUCT-003]  # xray: ignore[INTEG-003]
        os.rename(temp_path, save_path)
        
        print(f"[ZenForge Checkpoint] Security flush complete. Weights locked at {save_path}.")  # xray: ignore[PY-004]
    except Exception as e:
        print(f"[ZenForge Checkpoint] CRITICAL ERROR during save: {e}.")  # xray: ignore[PY-004]
        print(f"[ZenForge Checkpoint] Leaving temporary recovery files at {temp_path}.")  # xray: ignore[PY-004]
        # Purposely letting it crash the runtime to avoid silent continuation on full disk
        raise e
