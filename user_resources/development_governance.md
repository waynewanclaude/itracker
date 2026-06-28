# Development Governance

This document defines organizational and developer workflow requirements for building, changing, and maintaining `ocrone`. It applies to people and AI agents working on the software, not to end users running the application.

---

## 1. Role Boundaries

- **App user requirements** belong in `user_intent.md`. They describe what the application does, supported workflows, CLI behavior, inputs, outputs, and product boundaries.
- **Developer and organizational requirements** belong in this document. They describe repository discipline, local development environments, AI collaboration, reviewability, and change governance.
- **Documentation style rules** belong in `documentation_principle.md`.
- **Code design rules** belong in `design_and_coding_principle.md`.
- **Implementation suggestions** belong in `implementation_note.md`. They may guide code development, but they are advisory and must not override governing requirements.

---

## 2. Repository Requirements

- Development, documentation changes, and project modifications must happen inside a Git repository.
- If the workspace is not already inside a Git repository, the user must either provide the correct repository workspace or explicitly authorize the AI agent to create a local Git repository with `git init`.
- Git is the project revision log. Do not maintain manual revision history tables or author lists inside documents.
- Do not commit generated local development state, local virtual environments, caches, build outputs, or debug outputs unless the project intentionally promotes a specific generated artifact for distribution.
- Always update the patch version in `pyproject.toml` in every commit and push, even when it is only a document update or other minor event.

---

## 3. Repository Artifact Classes

- `planning_and_spec/` contains upstream planning, specification, governance, and documentation rules.
- `requirements.txt` contains the source dependency list used to build runtime environments.
- `user_resource/` contains user-supplied source assets that are required by the project but cannot be regenerated from the planning documents, requirements, or source code.
- Generated artifacts, including caches, logs, debug exports, built executables, build folders, generated PDFs, and work logs, must remain outside the permanent project baseline unless the user explicitly promotes them.

---

## 4. Information Flow

- Planning and specification documents are the upstream source of truth for implementation work.
- Information must flow forward from `planning_and_spec` into `src`, tests, packaging, and runtime artifacts.
- Do not extract application requirements, behavior definitions, or governance rules backward from `src` into planning documents unless the user explicitly asks to synchronize manual code changes back into the specs.
- If user-modified code needs to become the new source of truth, ask for explicit synchronization direction before updating planning documents from implementation details.

---

## 5. Environment Ownership

- The repository must not depend on a checked-in virtual environment.
- Developers should maintain their own local virtual environment for development and testing.
- The project folder may live beside any required virtual environment folder, but the virtual environment must not be part of the Git repository.
- End users should either build their own runtime environment from `requirements.txt` or use a packaged binary from `dist/`.
- Docker images may be used for reproducible runtime or test environments, but Docker build artifacts must not replace source-level dependency documentation.

### Recommended Local Developer Setup

Create the development environment from the project parent folder so the virtual environment is outside the application source tree:

```powershell
cd C:\Projects\codex
py -3.11 -m venv .venvs\ocrone
.\.venvs\ocrone\Scripts\Activate.ps1
cd .\ocrone
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

## 6. AI Collaboration Rules

- Before making development changes or further documentation edits, confirm that the workspace is inside a Git repository unless the user has explicitly authorized a bootstrap exception.
- Keep developer-specific AI workflow tracking files, such as plans, tasks, and walkthroughs, in a designated artifacts location. Do not mix them into product-facing documentation.
- When code changes are made, update the corresponding specification or user-facing documentation in the same change set.
- Keep edits incremental. Modify only the target sections or lines needed for the change.

---

## 7. Build Governance

- Build scripts are project source, not generated artifacts. Keep `build_exe.ps1` in the repository so binary generation remains reproducible.
- Build outputs are generated artifacts. Keep `build/`, `dist/`, PyInstaller `.spec` files, generated PDFs, and work logs out of the permanent project baseline unless the user explicitly promotes them.
- `ocrone.exe` is the CPU/lightweight distribution target. It may be built from a CPU-only PyTorch runtime environment.
- `ocrone-fast.exe` is the GPU/CUDA distribution target. It must be built from an environment where EasyOCR and DocLayout-YOLO can use CUDA through PyTorch.
- Before building `ocrone-fast.exe`, the build process must verify GPU runtime availability. The current preferred preflight is:
  - `torch.version.cuda` is not empty.
  - `torch.cuda.is_available()` returns true.
  - The CUDA-enabled PyTorch build can enumerate at least one CUDA device.
- If either GPU runtime verification check fails, the fast build must fail instead of producing a misleading CPU-only `ocrone-fast.exe`.
- Physical GPU presence is not sufficient proof that the fast build is valid. The build environment must contain a CUDA-capable PyTorch runtime that can see the NVIDIA GPU.
- Similar binary file sizes are not authoritative, but they are a useful warning signal. If `ocrone.exe` and `ocrone-fast.exe` are nearly the same size, verify the GPU runtime state before distribution.
- The build step must generate `dist/ocrone_sample_config.properties` with every supported configuration property set to its default value.
