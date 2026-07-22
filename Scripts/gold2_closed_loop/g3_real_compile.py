"""Compile a G3-Real candidate G3.cs into a loadable dll.

Writes a .csproj (from template) referencing Algorithm.CSharp.csproj next to
the candidate G3.cs, runs `dotnet build`, and returns the produced dll path +
its SHA256 (the real compiler_hash, replacing _StaticGen's "c0mp1ler"*8).
The candidate dll + its copy-local deps (Algorithm.CSharp.dll, Common.dll, ...)
land in <candidate_dir>/bin/, which LEAN loads via algorithm-location.

Spec: docs/superpowers/specs/2026-07-21-gold2-real-llm-reconstruction-design.md §3.4.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALGO_CSJPROJ = ROOT / "Algorithm.CSharp" / "QuantConnect.Algorithm.CSharp.csproj"
TMPL = ROOT / "Scripts" / "gold2_closed_loop" / "g3_real_candidate.csproj.tmpl"


@dataclass(frozen=True)
class CompileResult:
    """Outcome of a candidate compile.

    Fields:
        ok: True iff dotnet build succeeded and the dll exists on disk.
        dll_path: Expected dll path (may not exist when ok=False).
        compiler_hash: Lowercase hex sha256 of the dll bytes (64 chars), "" on failure.
        candidate_class: The candidate class name (echoed from the request).
        stderr_tail: Tail of the compiler stderr/stdout for diagnostics ("" on success).
    """
    ok: bool
    dll_path: Path
    compiler_hash: str
    candidate_class: str
    stderr_tail: str


def _default_dotnet() -> str:
    """Resolve the dotnet executable robustly.

    Prefer the documented absolute install (/usr/local/dotnet/dotnet) so the
    harness works without PATH configuration, then fall back to PATH lookup.
    Returns "" if neither resolves (caller may then short-circuit / skip).
    """
    explicit = Path("/usr/local/dotnet/dotnet")
    if explicit.exists() and explicit.is_file():
        return str(explicit)
    return shutil.which("dotnet") or ""


def compile_candidate(
    g3_cs: Path,
    *,
    candidate_id: str,
    candidate_class: str,
    dotnet: str | None = None,
    timeout: int = 180,
) -> CompileResult:
    """Compile G3.cs into <candidate_dir>/bin/<AssemblyName>.dll.

    Args:
        g3_cs: Path to the candidate C# source file (G3.cs).
        candidate_id: Short identifier used to name the assembly (G3Real_<id>).
        candidate_class: The candidate's C# class name (echoed in CompileResult).
        dotnet: Optional dotnet executable path. If None, auto-resolved via
            _default_dotnet() (checks /usr/local/dotnet/dotnet then PATH).
        timeout: Subprocess timeout in seconds.

    Returns:
        CompileResult with ok=True + dll_path + 64-char sha256 on success,
        ok=False + diagnostic stderr_tail on failure (compile error, timeout,
        dotnet not found).
    """
    cand_dir = Path(g3_cs).parent
    asm_name = f"G3Real_{candidate_id}"
    csproj = cand_dir / f"{asm_name}.csproj"
    dll = cand_dir / "bin" / f"{asm_name}.dll"

    dotnet_bin = dotnet or _default_dotnet()
    if not dotnet_bin:
        return CompileResult(
            ok=False,
            dll_path=dll,
            compiler_hash="",
            candidate_class=candidate_class,
            stderr_tail="dotnet executable not found (looked for /usr/local/dotnet/dotnet and PATH)",
        )

    tmpl = TMPL.read_text()
    csproj.write_text(
        tmpl
        .replace("{ASSEMBLY_NAME}", asm_name)
        .replace("{ALGO_CSHARP_CSJPROJ}", str(ALGO_CSJPROJ))
    )

    env = dict(os.environ)
    # Make sure dotnet's own directory is on PATH (so it can find its SDK).
    dotnet_dir = str(Path(dotnet_bin).resolve().parent)
    env["PATH"] = dotnet_dir + os.pathsep + env.get("PATH", "")

    try:
        proc = subprocess.run(
            [dotnet_bin, "build", str(csproj), "-c", "Debug", "--nologo"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cand_dir),
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        tail = (e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""))[-2000:]
        return CompileResult(
            ok=False, dll_path=dll, compiler_hash="",
            candidate_class=candidate_class, stderr_tail=f"timeout after {timeout}s: {tail}",
        )
    except FileNotFoundError as e:
        return CompileResult(
            ok=False, dll_path=dll, compiler_hash="",
            candidate_class=candidate_class, stderr_tail=f"dotnet not found: {e}",
        )

    if proc.returncode != 0 or not dll.exists():
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        return CompileResult(
            ok=False, dll_path=dll, compiler_hash="",
            candidate_class=candidate_class, stderr_tail=tail,
        )

    h = hashlib.sha256(dll.read_bytes()).hexdigest()
    return CompileResult(
        ok=True, dll_path=dll, compiler_hash=h,
        candidate_class=candidate_class, stderr_tail="",
    )
