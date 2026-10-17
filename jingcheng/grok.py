"""Bounded whole-shot CLI jobs. Never silently retry a paid request."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from .config import safe_id
from .util import load, lock, now, save, sha


def arguments(config, item):
    args = dict(item["arguments"])
    allowed = {"prompt", "duration", "aspect_ratio", "resolution_name", "images", "first_frame", "last_frame", "keyframes"}
    if set(args) - allowed: raise ValueError("Unverified reference_to_video parameters")
    duration = args.get("duration")
    if type(duration) is not int or not 1 <= duration <= 15:
        raise ValueError("This adapter's verified schema is 1–15 integer seconds; recheck CLI before changing it")
    if args.get("aspect_ratio") != "16:9" or args.get("resolution_name") not in {"720p", "480p"}:
        raise ValueError("Unverified video settings")
    if not args.get("prompt"):
        raise ValueError("Missing complete shot prompt")
    for key in ["first_frame", "last_frame"]:
        if key in args:
            args[key] = str(config.asset(args[key]).resolve(strict=True))
    args["images"] = [str(config.asset(p).resolve(strict=True)) for p in args.get("images", [])]
    keys = []
    for key in args.get("keyframes", []):
        t = key["timestamp_s"]
        if not 0 < t < duration or abs(t*3-round(t*3)) > 1e-5:
            raise ValueError("Keyframes must use the verified 1/3 second grid")
        keys.append({**key, "image": str(config.asset(key["image"]).resolve(strict=True))})
    if len(keys) > 4 or any(b["timestamp_s"]-a["timestamp_s"] < 1/3-1e-5 for a,b in zip(keys,keys[1:])):
        raise ValueError("At most four ordered, distinct internal keyframes")
    if keys:
        args["keyframes"] = keys
    return args


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k,v in value.items() if k not in {"thought","thinking","reasoning","signature"}}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        return "[binary omitted]"
    return value


def submit_one(config, batch_id, item, execute):
    ident = safe_id(item["id"])
    args = arguments(config, item)
    job = config.job(batch_id) / "grok" / ident
    if not execute:
        return {"id": ident, "job": str(job), "arguments": args, "submitted": False}
    if not config.get("grok.schema_confirmed") or not config.get("grok.privacy_ready"):
        raise ValueError("Check current CLI schema, login and authorized privacy setting; then set local readiness flags")
    with lock(job.parent / (ident + ".lock")):
        if job.exists():
            raise ValueError(f"Job already exists; inspect it and use a NEW candidate ID: {job}")
        job.mkdir(parents=True)
        prompt = job / "cli-prompt.txt"
        prompt.write_text(
            "Call reference_to_video exactly once with the JSON arguments below, unchanged. "
            "Do not use any other tool, plan, subagent or retry. Do not change account settings. "
            "Return the tool result. If the service fails, stop.\n" +
            json.dumps(args, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [config.tool("grok"), "--cwd", str(job),
               "--model", config.get("grok.controller_model"), "--reasoning-effort", config.get("grok.reasoning_effort"),
               "--no-subagents", "--no-plan", "--disable-web-search", "--tools", "reference_to_video",
               "--allow", "reference_to_video", "--permission-mode", "dontAsk", "--max-turns", "2",
               "--output-format", "streaming-json", "--prompt-file", str(prompt)]
        env = {**os.environ, "NO_COLOR":"1", "GROK_DISABLE_AUTOUPDATER":"1"}
        for name in ["http_proxy", "https_proxy", "all_proxy"]:
            if config.get("grok."+name):
                env[name] = env[name.upper()] = config.get("grok."+name)
        record = {"id":ident, "status":"starting", "created_at":now(), "requested_arguments":args,
                  "controller_model":config.get("grok.controller_model"),
                  "video_backend_version":"not inferred from controller model", "tool_call_count":0}
        manifest = job / "manifest.json"
        save(manifest, record)
        ids = set()
        with (job/"stderr.log").open("w", encoding="utf-8") as err, (job/"events.jsonl").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, cwd=job, env=env, stdout=subprocess.PIPE, stderr=err,
                                    text=True, encoding="utf-8")
            timed_out = threading.Event()
            def kill():
                timed_out.set()
                proc.kill()
            timer = threading.Timer(config.get("grok.timeout_seconds",900), kill)
            timer.start()
            try:
                for line in proc.stdout:
                    try:
                        event = clean(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") in {"thought","thinking","reasoning"}:
                        continue
                    log.write(json.dumps(event,ensure_ascii=False)+"\n"); log.flush()
                    if event.get("type") == "tool_call":
                        ids.add(event.get("toolCallId"))
                        record.update(tool_call_count=len(ids), status="submitted",
                                      actual_arguments=event.get("rawInput"))
                        record["arguments_match"] = event.get("rawInput") == args
                        if not record["arguments_match"]:
                            record["argument_mismatch_detected"] = True
                        if len(ids)>1:
                            record.update(status="unexpected_retry", error="CLI emitted another request; inspect service billing.")
                            proc.kill()
                    if event.get("type") == "tool_call_update" and event.get("status") in {"completed","failed"}:
                        result = event.get("rawOutput") or {}
                        record["tool_result"] = result
                        if record["status"] != "unexpected_retry" and event["status"] == "completed" and isinstance(result,dict) and result.get("path"):
                            source = Path(result["path"])
                            if not source.is_absolute():
                                source = job/source
                            source = source.resolve(strict=True)
                            target = job/"native.mp4"
                            if not target.exists():
                                shutil.copyfile(source,target)
                            record.update(status="generated_unreviewed",source_sha256=sha(target))
                        elif record["status"] != "unexpected_retry":
                            record["status"] = "failed"
                    save(manifest,record)
                record["exit_code"] = proc.wait()
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill(); proc.wait()
            if record.get("argument_mismatch_detected") and record["status"] == "generated_unreviewed":
                record["status"] = "generated_with_argument_mismatch"
            if timed_out.is_set():
                record["status"] = "timeout_outcome_unknown"
            elif record["status"] in {"starting","submitted"}:
                record["status"] = "outcome_unknown"
            record["finished_at"] = now()
            save(manifest,record)
        return record


def submit(config, manifest, execute=False):
    spec = load(manifest)
    batch_id = safe_id(spec["batch_id"])
    audio = spec.get("approved_audio", {})
    if execute:
        if audio.get("approved") is not True:
            raise ValueError("Approve the exact audio master before a paid video batch")
        audio_file = config.media(audio["reference"]).resolve(strict=True)
        if sha(audio_file) != audio.get("sha256"):
            raise ValueError("Approved audio master changed")
    jobs = spec["jobs"]
    if not jobs or len(jobs)>config.get("grok.max_jobs_per_batch",6):
        raise ValueError("Batch exceeds configured request budget")
    if len({safe_id(j["id"]) for j in jobs}) != len(jobs):
        raise ValueError("Duplicate job IDs")
    # Validate the WHOLE batch before any remote side effect.
    for j in jobs:
        arguments(config,j)
    seconds = sum(j["arguments"]["duration"] for j in jobs)
    if seconds>config.get("grok.max_requested_seconds_per_batch",90):
        raise ValueError("Batch exceeds configured generated-seconds budget")
    concurrency = max(1,min(config.get("grok.concurrency",2),len(jobs)))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(submit_one,config,batch_id,j,execute) for j in jobs]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"status":"failed","error":str(exc)})
    return {"batch_id":batch_id,"requested_seconds":seconds,"results":results,
            "complete":all(r.get("status")=="generated_unreviewed" for r in results) if execute else False}
