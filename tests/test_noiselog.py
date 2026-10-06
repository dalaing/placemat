"""placemat.noiselog: the continuous noise record (P012): its lines, its reader, surviving a write error,
and stopping cleanly on SIGTERM."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from placemat import lock, noiselog

ROOT = Path(__file__).resolve().parents[1]
FAKE = {"median_ms": 14.0, "spread": 0.02, "spread_old": 0.05, "n": 21}


class TestNoiselog(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        for f in sorted(self.d.rglob("*"), reverse=True):
            f.rmdir() if f.is_dir() else f.unlink()
        self.d.rmdir()

    def test_samples_and_read(self):
        out = self.d / "sub" / "noise.jsonl"
        with mock.patch.object(lock, "probe_detail", return_value=dict(FAKE)):
            noiselog.run(str(out), every=0.05, duration=0.12)
        rs = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual(len(rs), 3)                # at 0, 0.05 and 0.10 s
        self.assertEqual(set(rs[0]), {"t", "host", "median_ms", "spread", "spread_old", "load"})
        self.assertEqual((rs[0]["median_ms"], rs[0]["spread"], rs[0]["spread_old"]), (14.0, 0.02, 0.05))
        got = noiselog.read(str(out))
        self.assertEqual([r["t"] for r in got], [r["t"] for r in rs])
        self.assertEqual(noiselog.read(str(out), start=rs[1]["t"]), rs[1:])
        self.assertEqual(noiselog.read(str(out), start=rs[0]["t"], end=rs[1]["t"]), rs[:2])
        self.assertEqual(noiselog.read(str(self.d / "missing.jsonl")), [])

    def test_read_skips_bad_lines_and_sorts(self):
        f = self.d / "n.jsonl"
        f.write_text('{"t": 5, "spread": 0.1}\nnot json\n{"no_t": 1}\n{"t": 2, "spread": 0.3}\n{"t": 9')
        self.assertEqual([r["t"] for r in noiselog.read(str(f))], [2, 5])

    def test_default_path_from_env(self):
        with mock.patch.dict(os.environ, {"PLACEMAT_NOISELOG": str(self.d / "x.jsonl")}):
            self.assertEqual(noiselog.default_path(), str(self.d / "x.jsonl"))
            (self.d / "x.jsonl").write_text('{"t": 1}\n')
            self.assertEqual(noiselog.read(), [{"t": 1}])

    def test_survives_a_write_error(self):
        blocker = self.d / "file"
        blocker.write_text("")
        with mock.patch.object(lock, "probe_detail", return_value=dict(FAKE)), \
                mock.patch("sys.stderr") as err:
            self.assertEqual(noiselog.run(str(blocker / "noise.jsonl"), every=0.02, duration=0.03), 0)
        self.assertIn("cannot write", "".join(str(c) for c in err.write.call_args_list))

    def test_target_sample(self):
        with mock.patch.object(lock, "probe_detail", return_value={**FAKE, "where": "target", "target_load": 0.5}):
            r = noiselog.sample(["limactl", "shell", "vm", "--"])
        self.assertEqual((r["target"], r["where"], r["target_load"]), ("limactl shell vm --", "target", 0.5))

    def test_sigterm_stops_cleanly(self):
        out = self.d / "noise.jsonl"
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        p = subprocess.Popen([sys.executable, "-m", "placemat", "noiselog", "--every", "0.5", "--for", "60",
                              "--out", str(out)], env=env, stderr=subprocess.PIPE, text=True)
        try:
            t = time.time()
            while not (out.exists() and out.read_text().count("\n") >= 2) and time.time() - t < 30:
                time.sleep(0.05)
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(10), 0)
            self.assertEqual(p.stderr.read(), "")
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stderr.close()
        lines = out.read_text().splitlines()
        self.assertGreaterEqual(len(lines), 2)
        for x in lines:
            json.loads(x)                           # every line whole


if __name__ == "__main__":
    unittest.main()
