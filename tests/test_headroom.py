"""End-to-end tests: real processes, real memory, a real guard — scoped to this test's own
process tree so nothing else on the machine is touched. Run: python3 -m unittest -v tests/test_headroom.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "headroom")
GiB = 1024 ** 3
# Grows faster than one guard tick: a runaway the pause cannot catch in time.
FAST_HOG = ("import sys,time; b=b'\\x01'*(int(sys.argv[1])<<30); print('allocated', flush=True);"
            "time.sleep(float(sys.argv[2]))")
HOG = ("import os,sys,time; n=int(sys.argv[1]); b=[bytearray(os.urandom(256<<20)) for _ in range(n*4)];"
       "print('allocated', flush=True); time.sleep(float(sys.argv[2]))")


def load():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("headroom_mod", BIN)
    spec = importlib.util.spec_from_loader("headroom_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="headroom-test-")
        self.cfg_path = os.path.join(self.tmp, "config.json")
        self.env = dict(os.environ, HEADROOM_HOME=os.path.join(self.tmp, "state"),
                        HEADROOM_CONFIG=self.cfg_path)
        self.env.pop("HEADROOM_JOB", None)
        self.config(scope_pid=os.getpid(), notify=False, tick_s=0.5)
        self.daemon = None

    def tearDown(self):
        if self.daemon:
            self.daemon.kill()
            self.daemon.wait()

    def config(self, **kw):
        cur = {}
        if os.path.exists(self.cfg_path):
            with open(self.cfg_path) as f:
                cur = json.load(f)
        cur.update(kw)
        with open(self.cfg_path, "w") as f:
            json.dump(cur, f)

    def run_bg(self, *args, extra_env=None):
        env = dict(self.env, **(extra_env or {}))
        return subprocess.Popen([sys.executable, BIN] + list(args), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def start_guard(self):
        self.daemon = self.run_bg("daemon")

    def agent_total(self):
        hr = load()
        cfg = dict(hr.DEFAULTS, scope_pid=os.getpid())
        t = hr.process_table()
        return hr.agent_view(cfg, t, hr.children_map(t))[1]


class Queue(Base):
    def test_cpu_tokens_serialize_jobs_fifo(self):
        self.config(cpus=4, default_cpu=4)
        t0 = time.time()
        a = self.run_bg("run", "--", "sleep", "2")
        time.sleep(0.3)
        b = self.run_bg("run", "--", "sleep", "2")
        a.wait(), b.wait()
        self.assertGreaterEqual(time.time() - t0, 3.8, "second job must wait for the first")

    def test_small_jobs_share_cores(self):
        self.config(cpus=4, default_cpu=2)
        t0 = time.time()
        a = self.run_bg("run", "--", "sleep", "2")
        b = self.run_bg("run", "--", "sleep", "2")
        a.wait(), b.wait()
        self.assertLess(time.time() - t0, 3.5, "two 2-core jobs fit on 4 cores at once")

    def test_memory_reservation_queues_second_job(self):
        # Budget leaves room for one 3G reservation above what agents already use, not two.
        budget = self.agent_total() + 4 * GiB
        hr = load()
        self.config(agent_budget_pct=100 * budget / hr.TOTAL, admit_floor_pct=0)
        t0 = time.time()
        a = self.run_bg("run", "--mem", "3G", "--", "sleep", "2")
        time.sleep(0.3)
        b = self.run_bg("run", "--mem", "3G", "--", "sleep", "2")
        a.wait(), b.wait()
        self.assertGreaterEqual(time.time() - t0, 3.8, "3G + 3G does not fit a 4G headroom")

    def test_exit_code_passes_through(self):
        p = self.run_bg("run", "--", "sh", "-c", "exit 7")
        p.wait()
        self.assertEqual(p.returncode, 7)

    def test_learns_peak_memory(self):
        p = self.run_bg("run", "--name", "hog", "--", sys.executable, "-c", HOG, "1", "1.5")
        p.wait()
        hist = json.load(open(os.path.join(self.env["HEADROOM_HOME"], "history.json")))
        self.assertGreater(hist["hog"][-1], 0.9 * GiB)


class Guard(Base):
    def test_job_over_cap_is_killed_and_told_why(self):
        hr = load()
        self.config(job_cap_pct=100 * 1.5 * GiB / hr.TOTAL)
        self.start_guard()
        p = self.run_bg("run", "--", sys.executable, "-c", HOG, "3", "30")
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 137)
        self.assertIn("killed by the memory guard", err)
        self.assertIn("per-job cap", err)

    def test_unmanaged_runaway_is_killed(self):
        hr = load()
        self.config(runaway_pct=100 * 1.5 * GiB / hr.TOTAL)
        self.start_guard()
        time.sleep(1)
        p = subprocess.Popen([sys.executable, "-c", HOG, "3", "30"])  # not under `headroom run`
        p.wait(timeout=60)
        self.assertLess(p.returncode, 0, "the guard must have signalled it")

    def test_pause_over_budget_then_resume(self):
        base = self.agent_total()
        hr = load()
        # Budget 2G above today's agents: a 3G job is over budget (pause) but under 1.15x (no kill).
        self.config(agent_budget_pct=100 * (base + 2.2 * GiB) / hr.TOTAL, admit_floor_pct=0)
        self.start_guard()
        p = self.run_bg("run", "--mem", "1G", "--", sys.executable, "-c", HOG, "3", "4")
        deadline, paused = time.time() + 40, False
        jobs_dir = os.path.join(self.env["HEADROOM_HOME"], "jobs")
        while time.time() < deadline and not paused:
            for name in os.listdir(jobs_dir) if os.path.isdir(jobs_dir) else []:
                j = json.load(open(os.path.join(jobs_dir, name)))
                paused = paused or j.get("state") == "paused"
            time.sleep(0.3)
        self.assertTrue(paused, "the job should be paused once agents exceed the budget")
        self.config(agent_budget_pct=90)  # memory comes back
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("paused", err)
        self.assertIn("resumed", err)

    def test_paused_too_long_is_killed(self):
        base = self.agent_total()
        hr = load()
        self.config(agent_budget_pct=100 * (base + 1 * GiB) / hr.TOTAL, admit_floor_pct=0,
                    pause_max_s=3)
        self.start_guard()
        p = self.run_bg("run", "--mem", "1G", "--", sys.executable, "-c", HOG, "4", "30")
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 137, err)
        self.assertIn("paused", err)
        self.assertIn("memory never came back", err)

    def test_far_over_budget_kills_the_job(self):
        base = self.agent_total()
        hr = load()
        self.config(agent_budget_pct=100 * (base + 1 * GiB) / hr.TOTAL, admit_floor_pct=0)
        self.start_guard()
        p = self.run_bg("run", "--mem", "1G", "--", sys.executable, "-c", FAST_HOG, "3", "30")
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 137, err)
        self.assertIn("memory critical", err)


if __name__ == "__main__":
    unittest.main()
