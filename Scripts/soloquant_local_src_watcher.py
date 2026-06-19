#!/usr/bin/env python3
"""
SoloQuant Local Source Watcher
Scans the local-src/ directory for new/modified files, scores them
with LLM, and writes metrics to InfluxDB. High-scoring content is
copied into the crawled artifacts pipeline.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import soloquant_orchestrator as orchestrator

DEFAULT_LOCAL_SRC_DIR = "local-src"
DEFAULT_STATE_FILE = "local-src-state.json"
DEFAULT_WEIGHT_THRESHOLD = 5.0
DEFAULT_MAX_CONTENT_CHARS = 16000


def _load_state(state_path):
    if not state_path.exists():
        return {"processed_files": {}}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"processed_files": {}}


def _save_state(state_path, state):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_content(filepath):
    ext = filepath.suffix.lower()
    if ext in (".md", ".txt", ".url"):
        try:
            return filepath.read_text(encoding="utf-8", errors="ignore")[:DEFAULT_MAX_CONTENT_CHARS]
        except Exception:
            return ""
    if ext == ".pdf":
        return _extract_pdf_text(filepath)
    try:
        return filepath.read_text(encoding="utf-8", errors="ignore")[:DEFAULT_MAX_CONTENT_CHARS]
    except Exception:
        return ""


def _extract_pdf_text(pdf_path):
    try:
        result = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"],
                                capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return result.stdout[:DEFAULT_MAX_CONTENT_CHARS]
    except Exception:
        pass
    return ""


def _extract_urls(filepath):
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    return [l.strip() for l in content.splitlines() if l.strip().startswith(("http://", "https://"))]


def _llm_score(content, config):
    if not content.strip():
        return 0.0
    llm_config = config.get("llm") or config.get("glm") or {}
    api_key_env = str(llm_config.get("api-key-env-var") or "LLM_API_KEY")
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        api_key = str(llm_config.get("api-key-default") or "").strip()
    if not api_key:
        return _rule_based_score(content)
    base_url = str(llm_config.get("base-url") or "https://api.deepseek.com")
    model = str(llm_config.get("model") or "deepseek-v4-pro")
    payload = {"model": model, "messages": [
        {"role": "system", "content": "Quant info scorer. Score 0-10. 9-10=central bank/shock; 7-8=key data/bank report; 5-6=routine; 3-4=generic; 0-2=spam. Output only a float."},
        {"role": "user", "content": content[:8000]}], "temperature": 0.1}
    try:
        import urllib.request, re
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        match = re.search(r"(\d+\.?\d*)", text)
        if match:
            return round(max(0.0, min(10.0, float(match.group(1)))), 1)
    except Exception:
        pass
    return _rule_based_score(content)


def _rule_based_score(content):
    score = 3.0
    lower = content.lower()
    for kw in ["降息","加息","降准","rate cut","fomc","lpr","mlf","黑天鹅","央行","decision"]:
        if kw in lower: score += 1.5; break
    for kw in ["策略","alpha","factor","momentum","动量","backtest","回测","研报"]:
        if kw in lower: score += 0.5; break
    if len(content) > 5000: score += 0.5
    elif len(content) < 200: score -= 1.0
    return round(max(0.0, min(10.0, score)), 1)


def _escape_influx_key(value):
    return str(value).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _write_local_src_metric(filepath, local_src_dir, score, lag_minutes, config):
    now = datetime.now(timezone.utc)
    ts_ns = int(now.timestamp() * 1e9)
    ext = filepath.suffix.lower().lstrip(".") or "unknown"
    filename_hash = hashlib.md5(filepath.name.encode()).hexdigest()[:6]
    file_size = filepath.stat().st_size
    weight_tier = "high" if score >= 7 else ("medium" if score >= 4 else "low")
    fields = [f"file_size_bytes={file_size}i", f"weight_score={score}",
              f"discovered_at_lag_minutes={int(lag_minutes)}i", "processed=1i"]
    line = f"soloquant_local_src,file_ext={_escape_influx_key(ext)},category=manual,filename_hash={filename_hash},weight_tier={weight_tier} {','.join(fields)} {ts_ns}"
    influx_config = config.get("influxdb") or {}
    url = str(influx_config.get("url") or "http://localhost:8086")
    org = str(influx_config.get("org") or "lean")
    bucket = str(influx_config.get("bucket") or "quant")
    token_env = str(influx_config.get("token-env-var") or "INFLUXDB_TOKEN")
    token = os.getenv(token_env, "").strip()
    if not token: token = str(influx_config.get("token-default") or "").strip()
    if not token: return
    try:
        orchestrator.write_lines_to_influx([line], url, org, bucket, token)
    except Exception:
        pass


def watch_local_src(config):
    workflow_root = Path(config.get("workflow-root", "Results/soloquant"))
    local_src_dir = workflow_root / DEFAULT_LOCAL_SRC_DIR
    state_path = local_src_dir / DEFAULT_STATE_FILE
    local_src_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(state_path)
    processed = state.get("processed_files", {})
    new_files = []
    for f in local_src_dir.rglob("*"):
        if not f.is_file() or f.name in (DEFAULT_STATE_FILE, "README.md"): continue
        key = str(f.relative_to(local_src_dir))
        mtime = f.stat().st_mtime
        prev = processed.get(key)
        if prev is None or prev.get("mtime", 0) < mtime:
            new_files.append((f, key, mtime))
    if not new_files:
        return {"status": "ok", "new_files": 0, "results": []}
    results = []
    for filepath, key, mtime in new_files:
        ext = filepath.suffix.lower()
        if ext == ".url" or (filepath.parent.name == "urls" and ext == ".txt"):
            urls = _extract_urls(filepath)
            content = "\n".join(urls) if urls else ""
            score = _llm_score(content, config) if content else 0.0
        else:
            content = _read_content(filepath)
            if not content.strip(): continue
            score = _llm_score(content, config)
        lag_minutes = (time.time() - mtime) / 60.0
        _write_local_src_metric(filepath, local_src_dir, score, lag_minutes, config)
        if score >= DEFAULT_WEIGHT_THRESHOLD:
            artifact_root = config.get("artifact-root", str(workflow_root / "artifacts"))
            crawled_dir = Path(artifact_root) / "crawled" / "local_src"
            crawled_dir.mkdir(parents=True, exist_ok=True)
            dest = crawled_dir / f"{filepath.stem}-{hashlib.md5(key.encode()).hexdigest()[:6]}.json"
            dest.write_text(json.dumps({"title": filepath.stem, "url": f"local-src://{key}",
                "source": "local_src", "category": "local_src",
                "content": content[:DEFAULT_MAX_CONTENT_CHARS], "weight_score": score,
                "local_src_path": str(filepath),
                "crawled_at_utc": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False, indent=2), encoding="utf-8")
        processed[key] = {"mtime": mtime, "processed_at_utc": datetime.now(timezone.utc).isoformat(),
            "weight_score": score, "lag_minutes": round(lag_minutes, 1), "status": "processed"}
        results.append({"file": key, "score": score, "lag_minutes": round(lag_minutes, 1)})
    state["processed_files"] = processed
    _save_state(state_path, state)
    return {"status": "ok", "new_files": len(new_files), "results": results}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    config = orchestrator.load_config(args.config) if args.config else {}
    report = watch_local_src(config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "ok" else 1

if __name__ == "__main__":
    raise SystemExit(main())
