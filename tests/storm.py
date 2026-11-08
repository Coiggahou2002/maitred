"""Load test against the installed guard: ~44 GB of incompressible demand, six queued jobs and
four unqueued runaways. Stops everything itself if free memory drops below 10 %.
Run: python3 tests/storm.py (needs `maitred install`)."""
import json, os, subprocess, sys, time, importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader("hr", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "maitred"))
spec = importlib.util.spec_from_loader("hr", loader); hr = importlib.util.module_from_spec(spec); loader.exec_module(hr)
GiB = hr.GiB
GROW = ("import os,sys,time; n=int(sys.argv[1]); b=[]\n"
        "for _ in range(n*2): b.append(bytearray(os.urandom(512<<20))); time.sleep(0.25)\n"
        "time.sleep(float(sys.argv[2]))")
HR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "maitred")
procs = []
for i in range(6):
    procs.append(("queued", subprocess.Popen([HR, "run", "--mem", "4G", "--cpu", "2", "--name", f"storm-q{i}", "--",
                  sys.executable, "-c", GROW, "4", "15"], stderr=subprocess.PIPE, text=True)))
for i in range(4):
    procs.append(("unqueued", subprocess.Popen([sys.executable, "-c", GROW, "5", "40"])))
cfg = hr.load_config()
t0 = time.time(); samples = []; worst_jitter = 0; aborted = False
while time.time() - t0 < 150 and [p.poll() for _, p in procs].count(None):
    a = time.time(); time.sleep(0.1); worst_jitter = max(worst_jitter, time.time() - a - 0.1)
    m = hr.memory(); t = hr.process_table(); sz, tot = hr.agent_view(cfg, t, hr.children_map(t))
    samples.append((round(time.time() - t0, 1), round(m["free"]), m["pressure"], round(sum(sz.values()) / GiB, 1)))
    if m["free"] < 10:
        aborted = True
        for _, p in procs: p.kill()
        break
for _, p in procs:
    try: p.wait(timeout=60)
    except subprocess.TimeoutExpired: p.kill()
res = {"aborted_by_safety_net": aborted, "seconds": round(time.time() - t0),
       "min_free_pct": min(s[1] for s in samples), "max_pressure": max(s[2] for s in samples),
       "max_agent_work_gb": max(s[3] for s in samples), "budget_gb": round(cfg["agent_budget_pct"] / 100 * hr.TOTAL / GiB, 1),
       "worst_sleep_jitter_ms": round(worst_jitter * 1000),
       "exit_codes": [(k, p.returncode) for k, p in procs],
       "queued_stderr": [p.stderr.read().strip().splitlines()[-1:] for k, p in procs if k == "queued"]}
print(json.dumps(res, ensure_ascii=False, indent=1))
json.dump({"res": res, "samples": samples}, open("/tmp/maitred-storm.json", "w"))
