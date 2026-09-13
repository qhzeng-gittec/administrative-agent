"""Verify a small subset of benchmark assumptions against real Docker and npm.

Creates only uniquely named disposable containers/network and temporary files.
Never operates on a user container or runs model-generated commands.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


def command(args: list[str], *, cwd=None, timeout=30):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return {"exit_code": result.returncode, "output": (result.stdout + result.stderr)[-5000:]}


def verify(output: Path):
    name = "starbridge-check-" + uuid.uuid4().hex[:10]
    network, server = name + "-net", name + "-db"
    checks = []
    created_network = created_server = False
    try:
        result = command(["docker", "network", "create", network])
        if result["exit_code"]: raise RuntimeError(result)
        created_network = True
        result = command(["docker", "run", "-d", "--rm", "--name", server, "--network", network,
                          "-e", "POSTGRES_PASSWORD=fixture-only-password", "postgres:15-alpine"])
        if result["exit_code"]: raise RuntimeError(result)
        created_server = True
        for _ in range(30):
            if command(["docker", "exec", server, "pg_isready", "-U", "postgres"])["exit_code"] == 0:
                break
            time.sleep(0.3)
        else:
            raise RuntimeError("临时数据库未就绪")
        scenarios = [
            ("container_localhost_fails", "localhost", "fixture-only-password", network, False),
            ("service_name_connects", server, "fixture-only-password", network, True),
            ("wrong_password_fails", server, "deliberately-wrong", network, False),
            ("different_network_fails", server, "fixture-only-password", "bridge", False),
        ]
        for label, host, password, client_network, expected in scenarios:
            result = command(["docker", "run", "--rm", "--network", client_network,
                              "-e", "PGCONNECT_TIMEOUT=3", "-e", "PGPASSWORD=" + password,
                              "postgres:15-alpine", "psql", "-h", host, "-U", "postgres", "-c", "SELECT 1;"])
            checks.append({"check": label, "passed": (result["exit_code"] == 0) == expected, **result})
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm: raise RuntimeError("未安装 npm，CI 环境验证未执行")
        with tempfile.TemporaryDirectory(prefix="starbridge-ci-") as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(json.dumps({"name": "support-fixture", "version": "1.0.0",
                "private": True, "scripts": {"test": "node test.cjs"}}), encoding="utf-8")
            lock = {"name": "support-fixture", "version": "1.0.0", "lockfileVersion": 3,
                    "packages": {"": {"name": "support-fixture", "version": "1.0.0"}}}
            (root / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
            result = command([npm, "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=root)
            checks.append({"check": "valid_lock_install", "passed": result["exit_code"] == 0, **result})
            (root / "test.cjs").write_text("require('node:assert/strict').equal(0, 100);", encoding="utf-8")
            result = command([npm, "test"], cwd=root)
            checks.append({"check": "test_assertion_failure", "passed": result["exit_code"] != 0 and "AssertionError" in result["output"], **result})
            (root / "test.cjs").write_text("require('node:assert/strict').equal(100, 100);", encoding="utf-8")
            result = command([npm, "test"], cwd=root)
            checks.append({"check": "corrected_test_passes", "passed": result["exit_code"] == 0, **result})
    finally:
        if created_server:
            result = command(["docker", "rm", "-f", server])
            if result["exit_code"]: raise RuntimeError(f"临时容器清理失败: {result}")
        if created_network:
            result = command(["docker", "network", "rm", network])
            if result["exit_code"]: raise RuntimeError(f"临时网络清理失败: {result}")
    report = {"execution": "real_docker_and_npm", "checks": checks,
              "passed": all(c["passed"] for c in checks),
              "scope": "7 个环境机制检查；没有将 252 个合成案例宣称为真实容器实验"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    verify(Path("evaluation/support-environment-check.json"))
