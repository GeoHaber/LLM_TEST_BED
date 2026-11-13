# ZEN_Forge Archive

**These scripts are deprecated.** The distillation pipeline now lives in
`LlamaFactory/scripts/` and uses `zen_core_libs` for shared infrastructure.

| File | Replaced By |
|------|-------------|
| `train.py` | `LlamaFactory/scripts/run_student_forge.py` |
| `real_distillation.py` | `LlamaFactory/scripts/multi_teacher_generate.py` + `purify_teacher_outputs.py` |
| `run_distillation_pipeline.py` | `LlamaFactory/scripts/orchestrate_pipeline.py` |
| `data_sanitizer.py` | `LlamaFactory/scripts/validate_datasets.py` |
| `checkpoint_manager.py` | Built into `run_student_forge.py` (ForgeState) |
| `ui/` | `LlamaFactory/src/llamafactory/webui/` (LLaMA Board distillation tabs) |

Kept for reference only. Do not import or call these files.
