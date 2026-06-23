"""ToolRuntime：5 类工具 + 沙箱 + 自动审计。

设计原则：
- 每个工具方法内部走 ctx.tool("name", lambda: ...) 包装，**不**直接调 checkpointer
  —— 自动获得缓存（activity 层）、审计（tool_calls 表）、可中断（未来可在 ctx.tool 加）。
- thread 级 workdir：ToolRuntime(thread_id, root) → {root}/af-{thread_id}/，
  测试用 tempfile.TemporaryDirectory() 自定义 root，不留垃圾。
- run_cmd 安全性：路径含 ".." 直接抛 PermissionError；命令必须以白名单前缀开头。
- 工具失败抛异常，由 ctx.tool → ctx.activity 自动记录 status="exception"。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional


class MCPToolProvider:
    """MCP 工具提供者抽象。子类实现具体协议（stdio / HTTP / mock）。

    MCP（Model Context Protocol）是 Anthropic 提出的外部工具标准协议。
    本抽象仅预留接口，不实现完整 MCP 客户端。
    """

    def list_tools(self) -> List[Dict[str, Any]]:
        """返回可用工具列表，每个工具至少含 ``name`` 字段。"""
        raise NotImplementedError

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """调用指定工具，返回工具原始结果（任意可 JSON 序列化类型）。"""
        raise NotImplementedError


# run_cmd 命令白名单：只允许以下前缀的程序名。
#
# 重要安全说明（CR 2026-06-17 指出）：
#   本沙箱**不**是 OS 级安全边界。`_check_no_dotdot` 只能拦 ASCII 的 ".."，但
#   `cat /etc/passwd` 这类带绝对路径的命令会绕过校验；白名单只对"首段 basename"
#   做检查，无法防御 `git config core.fsmonitor` 这类 Git 内部机制。
#
#   设计原则：白名单**只**放 argv 完全可控的命令：
#     - `pytest`：纯测试执行器，参数是测试文件路径
#     - `git diff` / `git status` / `git rev-parse` 等只读子命令
#     - `ls` / `cat` / `pwd` / `echo`：受 `_resolve_within_workdir` 限制
#
#   故意不放：
#     - `python` / `python3` — `-c '...'` 是图灵完备的，可执行任意代码
#     - `cd` — 会被 `cwd=self.workdir` 覆盖且容易混淆路径
#     - `rm` / `mv` / `cp` / `chmod` / `sudo` — 明显危险
#     - `curl` / `wget` / `ssh` — 网络外联
#
#   真实 LLM + 多租户场景下，**必须**接 Docker / gVisor 等真沙箱，
#   本白名单只防止「LLM 误调 rm -rf」这类意外。
_CMD_ALLOWED_PREFIXES = (
    "pytest",
    "ls",
    "cat",
    "echo",
    "pwd",
    "sleep",  # 用于超时测试，无副作用
    "git",
    "diff",
    "patch",
)

# 检测 .. 路径穿越：路径任意位置出现 ".." 作为独立 segment
_DOTDOT_RE = re.compile(r"(^|/)\.\.($|/)")


def _check_no_dotdot(path: str) -> None:
    """路径中含 '..' 视作越权，抛 PermissionError。

    检查规则：'..' 必须作为路径段（/.. 或 ..\0）出现，避免误伤 '..foo' 这类合法
    文件名；同时跳过带引号的参数以减小误报面。
    """
    if not path:
        return
    # 拆出 token 防止 '..' 出现在引号字符串内被误判（如 commit message）
    for token in re.findall(r'"[^"]*"|\'[^\']*\'|\S+', path):
        if token.startswith(("'", '"')):
            continue
        if _DOTDOT_RE.search(token):
            raise PermissionError(
                f"run_cmd 拒绝包含 '..' 路径穿越的输入: {repr(token)}"
            )


def _check_paths_in_workdir(cmd: str, cmd_name: str, workdir: str) -> None:
    """对 cat/ls 类文件读命令，校验所有路径参数都落在 workdir 内。

    解决 CR 2026-06-17 1.2 提出的问题：白名单只校验首段 basename，
    `cat /etc/passwd` 这种带绝对路径的命令原本会被放行。

    实现：shlex 拆 token → 跳过选项（以 - 开头）→ 解析为绝对路径 →
    校验前缀在 workdir 内。
    """
    import shlex
    workdir_real = os.path.realpath(workdir)
    for token in shlex.split(cmd)[1:]:  # 跳过命令名本身
        if not token or token.startswith("-"):
            continue
        # 引号已被 shlex 剥掉
        if os.path.isabs(token):
            # 绝对路径：必须落在 workdir 内
            real = os.path.realpath(token)
            if not (real == workdir_real or real.startswith(workdir_real + os.sep)):
                raise PermissionError(
                    f"run_cmd {repr(cmd_name)} 拒绝绝对路径 {repr(token)}："
                    f"必须落在 workdir {workdir_real} 内"
                )
        # 相对路径：subprocess 已经在 cwd=workdir 下跑，自动安全，无需校验


def _resolve_within_workdir(workdir: str, path: str) -> str:
    """把相对路径解析为 workdir 之下的绝对路径。绝对路径也必须落在 workdir 内。"""
    if os.path.isabs(path):
        abs_path = os.path.realpath(path)
    else:
        abs_path = os.path.realpath(os.path.join(workdir, path))
    real_workdir = os.path.realpath(workdir)
    if not (abs_path == real_workdir or abs_path.startswith(real_workdir + os.sep)):
        raise PermissionError(f"路径 {repr(path)} 超出 workdir 沙箱 {repr(workdir)}")
    return abs_path


class ToolRuntime:
    """线程级沙箱：文件 / Patch / Shell / Git 工具的统一入口。

    用法：
        rt = ToolRuntime(thread_id="abc", root=tempdir)
        text = rt.read_file("foo.py")                  # 直接调用
        # 或
        text = ctx.tool("read_file", lambda: rt.read_file("foo.py"),
                        input_summary="foo.py")        # 走 ctx.activity 缓存 + 审计
    """

    def __init__(self, thread_id: str, root: str = "/tmp"):
        self.thread_id = thread_id
        self.root = root
        self.workdir = os.path.join(root, f"af-{thread_id}")
        os.makedirs(self.workdir, exist_ok=True)
        self._mcp_providers: List[MCPToolProvider] = []

    # —— 文件 —— #

    def read_file(self, path: str) -> str:
        """读文本文件。path 相对 workdir 或绝对（必须落在 workdir 内）。"""
        full = _resolve_within_workdir(self.workdir, path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """写文件，自动建父目录。返回 {"path", "bytes"}。"""
        full = _resolve_within_workdir(self.workdir, path)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = content.encode("utf-8")
        with open(full, "wb") as f:
            f.write(data)
        return {"path": path, "bytes": len(data)}

    def list_dir(self, path: str = ".") -> List[str]:
        """列目录条目名。path 相对 workdir 或绝对（必须落在 workdir 内）。"""
        full = _resolve_within_workdir(self.workdir, path)
        if not os.path.isdir(full):
            raise FileNotFoundError(f"不是目录或不存在: {repr(path)}")
        return sorted(os.listdir(full))

    # —— Patch —— #

    def apply_patch(self, path: str, unified_diff: str) -> Dict[str, Any]:
        """应用 unified diff 到 workdir 下指定文件。

        实现策略：把 diff 写到临时文件，在 workdir 内调 `patch -p1 --dry-run` 校验
        hunk 匹配；校验通过后再 `patch -p1` 真正写入。失败时抛 RuntimeError，错误
        信息带 patch 的 stderr 摘要。

        CR 2026-06-17 2.1: 空 diff 静默"成功"且创建空文件 —— 显式拒绝。
        """
        # CR 2026-06-17 2.1: 拒绝空 diff，避免静默创建空文件
        if not unified_diff or not unified_diff.strip():
            raise ValueError(
                f"apply_patch 需要非空 unified_diff（path={repr(path)}）"
            )
        # CR 2026-06-17 3.6: 限制 diff 大小，避免 OOM
        if len(unified_diff) > 1_000_000:
            raise ValueError(
                f"apply_patch diff 超过 1MB 限制（{len(unified_diff)} 字节）"
            )
        full = _resolve_within_workdir(self.workdir, path)
        # patch 需要文件存在（即使是空文件）；不存在则建空文件
        if not os.path.exists(full):
            open(full, "wb").close()

        # 把 diff 写到 workdir 内的临时文件，避免受 cmd 白名单影响
        diff_tmp = os.path.join(self.workdir, ".af-tmp.patch")
        with open(diff_tmp, "w", encoding="utf-8") as f:
            f.write(unified_diff)

        # 先 dry-run 校验
        dry = subprocess.run(
            ["patch", "-p1", "--dry-run", "--input", diff_tmp],
            cwd=self.workdir,
            capture_output=True,
            text=True,
        )
        if dry.returncode != 0:
            # 清理临时 diff
            try:
                os.remove(diff_tmp)
            except OSError:
                pass
            err = (dry.stderr or "").strip() or (dry.stdout or "").strip()
            raise RuntimeError(
                f"apply_patch 校验失败（hunk 不匹配？）: {err[:300]}"
            )

        # 真正应用
        real = subprocess.run(
            ["patch", "-p1", "--input", diff_tmp],
            cwd=self.workdir,
            capture_output=True,
            text=True,
        )
        # 清理临时 diff
        try:
            os.remove(diff_tmp)
        except OSError:
            pass

        if real.returncode != 0:
            err = (real.stderr or "").strip() or (real.stdout or "").strip()
            raise RuntimeError(f"apply_patch 写入失败: {err[:300]}")

        # 估算 hunks 数（@@ ... @@ 行数）
        hunks = sum(1 for line in unified_diff.splitlines()
                    if line.startswith("@@ "))
        return {"path": path, "applied": True, "hunks": hunks}

    # —— Shell —— #

    def run_cmd(self, cmd: str, timeout: float = 60.0) -> Dict[str, Any]:
        """在 workdir 内跑 shell 命令。

        返回 {"stdout", "stderr", "exit_code", "duration_ms"}。
        失败规则：
        - cmd 含 '..' 路径 → PermissionError
        - cmd 不以白名单前缀开头 → PermissionError
        - 超时 → 杀子进程后抛 TimeoutError
        """
        if not cmd or not cmd.strip():
            raise ValueError("run_cmd 需要非空 cmd 字符串")
        _check_no_dotdot(cmd)
        # 提取首段（命令名）做白名单校验
        first_token = cmd.strip().split()[0]
        # 兼容 `python -c '...'`、`git diff` 这类：取 basename
        first_base = os.path.basename(first_token)
        if not any(first_base == p or first_base == p + ".exe"
                   for p in _CMD_ALLOWED_PREFIXES):
            raise PermissionError(
                f"run_cmd 拒绝未在白名单中的命令: {repr(first_base)} "
                f"（允许: {', '.join(_CMD_ALLOWED_PREFIXES)}）"
            )
        # 对文件读命令（cat/ls），强制参数路径在 workdir 内（CR 2026-06-17:1.2）
        if first_base in ("cat", "ls"):
            _check_paths_in_workdir(cmd, first_base, self.workdir)

        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # 超时：subprocess.run 已杀进程
            duration_ms = (time.time() - t0) * 1000
            raise TimeoutError(
                f"run_cmd 超时（{timeout}s）: {cmd[:80]}"
            ) from exc

        duration_ms = (time.time() - t0) * 1000
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
        }

    # —— Git —— #

    def git_diff(self, ref1: str = "HEAD", ref2: Optional[str] = None) -> str:
        """在 workdir 内跑 git diff。非 git 仓库或 git 不存在时返回 ''。"""
        # 先快速判定是否 git 仓库：.git 目录或上层有 .git
        cwd = self.workdir
        # 简单探针：调 `git rev-parse --git-dir`
        try:
            probe = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=cwd, capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return ""
        if probe.returncode != 0:
            return ""
        # 是 git 仓库：跑 diff
        args = ["git", "diff", ref1]
        if ref2:
            args.append(ref2)
        try:
            proc = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout

    # —— MCP 工具适配 —— #

    def register_mcp(self, provider: MCPToolProvider) -> None:
        """注册一个 MCP 工具提供者。

        同一提供者可多次注册（幂等）；调用方可在节点内通过 ``call_mcp`` 调用。
        """
        if provider not in self._mcp_providers:
            self._mcp_providers.append(provider)

    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        """聚合所有已注册 MCP 提供者的工具列表。"""
        tools: List[Dict[str, Any]] = []
        for provider in self._mcp_providers:
            try:
                tools.extend(provider.list_tools())
            except Exception:
                # 单个提供者故障不影响其他提供者
                continue
        return tools

    def call_mcp(self, name: str, arguments: Dict[str, Any]) -> Any:
        """按名称调用 MCP 工具。

        遍历已注册提供者，首个 ``list_tools()`` 含该 ``name`` 的提供者获得调用权。
        未找到时抛 ``ValueError``。
        """
        for provider in self._mcp_providers:
            try:
                tool_names = {t.get("name") for t in provider.list_tools() if isinstance(t, dict)}
                if name in tool_names:
                    return provider.call_tool(name, arguments)
            except Exception:
                continue
        raise ValueError(f"MCP 工具未找到: {repr(name)}（已注册提供者: {len(self._mcp_providers)} 个）")

    # —— 清理 —— #

    def cleanup(self) -> None:
        """删除 workdir 沙箱。幂等。"""
        if os.path.isdir(self.workdir):
            shutil.rmtree(self.workdir, ignore_errors=True)
