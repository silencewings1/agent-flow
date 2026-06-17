"""验证 ToolRuntime 的 13 个测试用例。

设计：
- 用 tempfile.TemporaryDirectory() 给每个测试一个独立 workdir，结束后自动清理。
- test_tool_caching 通过构造真实 StateGraph + 节点内 ctx.tool 包装，验证
  activity 缓存机制自动让第二次 read_file 不实际触达 fs（用 mock patch 计数）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import Dict, Tuple
from unittest import mock

from agentflow import (
    Checkpointer,
    StateGraph,
    StateSchema,
    START,
    END,
    ToolRuntime,
)


# —— 测试 fixture —— #

class _ToolFixture:
    """每个测试一个临时 root，结束自动清理。"""

    def setup(self):
        self._root = tempfile.mkdtemp(prefix="af-test-")
        self.rt = ToolRuntime(thread_id="t", root=self._root)

    def teardown(self):
        # 即使测试已调 cleanup() 也安全
        if os.path.isdir(self._root):
            shutil.rmtree(self._root, ignore_errors=True)


# —— 1) read_file 存在的文件 —— #

def test_read_file_existing():
    f = _ToolFixture()
    f.setup()
    try:
        path = os.path.join(f.rt.workdir, "hello.txt")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("hi 你好")
        content = f.rt.read_file("hello.txt")
        assert content == "hi 你好", f"内容不匹配: {content!r}"
        print("✅ test_read_file_existing 通过")
    finally:
        f.teardown()


# —— 2) read_file 不存在的文件 —— #

def test_read_file_missing():
    f = _ToolFixture()
    f.setup()
    try:
        try:
            f.rt.read_file("does_not_exist.txt")
        except FileNotFoundError as e:
            print(f"✅ test_read_file_missing 通过 ({e})")
            return
        raise AssertionError("应抛 FileNotFoundError")
    finally:
        f.teardown()


# —— 3) write_file 新路径（含父目录） —— #

def test_write_file_new_path():
    f = _ToolFixture()
    f.setup()
    try:
        result = f.rt.write_file("a/b/c/new.txt", "hello world")
        assert result["path"] == "a/b/c/new.txt"
        assert result["bytes"] == len("hello world".encode("utf-8"))
        full = os.path.join(f.rt.workdir, "a/b/c/new.txt")
        assert os.path.isfile(full), f"文件未创建: {full}"
        with open(full, "r", encoding="utf-8") as fp:
            assert fp.read() == "hello world"
        print("✅ test_write_file_new_path 通过")
    finally:
        f.teardown()


# —— 4) write_file 覆盖已有文件 —— #

def test_write_file_overwrite():
    f = _ToolFixture()
    f.setup()
    try:
        full = os.path.join(f.rt.workdir, "x.txt")
        with open(full, "w", encoding="utf-8") as fp:
            fp.write("OLD")
        result = f.rt.write_file("x.txt", "NEW-CONTENT")
        assert result["bytes"] == len("NEW-CONTENT")
        with open(full, "r", encoding="utf-8") as fp:
            assert fp.read() == "NEW-CONTENT"
        print("✅ test_write_file_overwrite 通过")
    finally:
        f.teardown()


# —— 5) apply_patch 有效 diff —— #

def test_apply_patch_valid():
    f = _ToolFixture()
    f.setup()
    try:
        target = os.path.join(f.rt.workdir, "foo.py")
        with open(target, "w", encoding="utf-8") as fp:
            fp.write("def foo():\n    return 1\n")
        # 构造 unified diff（与 patch -p1 兼容：使用 a/foo.py / b/foo.py 头）
        diff = (
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def foo():\n"
            "-    return 1\n"
            "+    return 42\n"
        )
        result = f.rt.apply_patch("foo.py", diff)
        assert result["applied"] is True
        assert result["hunks"] == 1
        assert result["path"] == "foo.py"
        with open(target, "r", encoding="utf-8") as fp:
            new_content = fp.read()
        assert "return 42" in new_content
        assert "return 1" not in new_content
        print("✅ test_apply_patch_valid 通过")
    finally:
        f.teardown()


# —— 6) apply_patch 无效 diff（hunk 匹配失败） —— #

def test_apply_patch_invalid():
    f = _ToolFixture()
    f.setup()
    try:
        target = os.path.join(f.rt.workdir, "bar.py")
        with open(target, "w", encoding="utf-8") as fp:
            fp.write("def bar():\n    return 1\n")
        # 构造一个上下文不匹配的 diff
        bad_diff = (
            "--- a/bar.py\n"
            "+++ b/bar.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def WRONG():\n"
            "-    return 1\n"
            "+    return 99\n"
        )
        try:
            f.rt.apply_patch("bar.py", bad_diff)
        except RuntimeError as e:
            print(f"✅ test_apply_patch_invalid 通过 ({str(e)[:60]}...)")
            return
        raise AssertionError("应抛 RuntimeError")
    finally:
        f.teardown()


# —— 7) run_cmd 成功 —— #

def test_run_cmd_success():
    f = _ToolFixture()
    f.setup()
    try:
        # CR 2026-06-17: python3 移出白名单；改用 echo 验证 exit 0 路径
        result = f.rt.run_cmd("echo 1")
        assert result["exit_code"] == 0, f"exit_code 应为 0，实际 {result['exit_code']}"
        assert result["stdout"].strip() == "1", f"stdout 应为 '1'，实际 {result['stdout']!r}"
        assert result["duration_ms"] >= 0
        print("✅ test_run_cmd_success 通过")
    finally:
        f.teardown()


# —— 8) run_cmd 非零退出 —— #

def test_run_cmd_nonzero_exit():
    f = _ToolFixture()
    f.setup()
    try:
        # 改用 cat 一个不存在的文件来触发非零退出
        result = f.rt.run_cmd("cat nonexistent_file_xyz")
        assert result["exit_code"] != 0, f"exit_code 应非零，实际 {result['exit_code']}"
        # run_cmd 自身不抛异常（非零退出是正常结果，由调用方处理）
        print(f"✅ test_run_cmd_nonzero_exit 通过 (exit_code={result['exit_code']})")
    finally:
        f.teardown()


# —— 9) run_cmd 拒绝含 '..' 路径 —— #

def test_run_cmd_rejects_dotdot():
    f = _ToolFixture()
    f.setup()
    try:
        try:
            f.rt.run_cmd("cat ../../etc/passwd")
        except PermissionError as e:
            assert ".." in str(e), f"错误信息应提及 '..'，实际 {e}"
            print(f"✅ test_run_cmd_rejects_dotdot 通过 ({e})")
            return
        raise AssertionError("应抛 PermissionError")
    finally:
        f.teardown()


# —— 10) run_cmd 超时 —— #

def test_run_cmd_timeout():
    f = _ToolFixture()
    f.setup()
    try:
        # CR 2026-06-17: python3 移出白名单；改用 sleep 触发超时
        try:
            f.rt.run_cmd("sleep 5", timeout=0.5)
        except TimeoutError as e:
            assert "超时" in str(e) or "timeout" in str(e).lower()
            print(f"✅ test_run_cmd_timeout 通过 ({e})")
            return
        raise AssertionError("应抛 TimeoutError")
    finally:
        f.teardown()


# —— 11) 工具缓存：同 thread 同 node 同 step 调两次 read_file，fs 只读 1 次 —— #

def test_tool_caching():
    """通过真实 StateGraph 走 ctx.tool 路径：第二次 read_file 命中 activity 缓存。

    实现思路：
    1. 构造 StateGraph，节点内调 ctx.tool("read_file", lambda: rt.read_file(...))。
    2. 用 mock.patch 替换内置 open 计数；首次 invoke 后第二次 invoke，fs 访问次数
       不应增加。
    3. 验证同 thread 重跑时 fs 访问保持 1 次。
    """
    _root = tempfile.mkdtemp(prefix="af-cache-")
    try:
        # 准备 workdir 与文件
        rt = ToolRuntime(thread_id="tc", root=_root)
        target = os.path.join(rt.workdir, "data.txt")
        with open(target, "w", encoding="utf-8") as fp:
            fp.write("PAYLOAD")

        # 计数：用 mock 监视 os.path.isfile / open（最直观：监视 read_file 内用到的 open）
        access_count = {"open": 0}
        real_open = open

        def counting_open(*args, **kwargs):
            access_count["open"] += 1
            return real_open(*args, **kwargs)

        # 节点函数：调 ctx.tool 两次
        def read_twice(state, ctx):
            text1 = ctx.tool("read_file",
                             lambda: rt.read_file("data.txt"),
                             input_summary="data.txt")
            text2 = ctx.tool("read_file",
                             lambda: rt.read_file("data.txt"),
                             input_summary="data.txt")
            return {"first": text1, "second": text2, "log": [text1, text2]}

        g = StateGraph(StateSchema())
        g.add_node("reader", read_twice)
        g.add_edge(START, "reader")
        g.add_edge("reader", END)
        app = g.compile(Checkpointer())

        with mock.patch("builtins.open", side_effect=counting_open):
            r1 = app.invoke({}, thread_id="tc")
        assert r1.status == "completed", r1.status
        # 节点内 read_file 被调 2 次，但内置 open 被调 1 次：第二次命中缓存
        assert access_count["open"] == 1, (
            f"fs open 应只发生 1 次（第二次走缓存），实际 {access_count['open']}"
        )
        # 状态正确
        assert r1.state["first"] == "PAYLOAD"
        assert r1.state["second"] == "PAYLOAD"

        # 再次 invoke 同一 thread：缓存仍命中，fs 访问次数不应增加
        with mock.patch("builtins.open", side_effect=counting_open):
            r2 = app.invoke({}, thread_id="tc")
        assert r2.status == "completed"
        assert access_count["open"] == 1, (
            f"重入 invoke 后 fs 访问仍应为 1（缓存命中），实际 {access_count['open']}"
        )

        print(f"✅ test_tool_caching 通过 (fs open 调用次数={access_count['open']})")
    finally:
        if os.path.isdir(_root):
            shutil.rmtree(_root, ignore_errors=True)


# —— 12) cleanup 后 workdir 不存在 —— #

def test_cleanup_removes_workdir():
    f = _ToolFixture()
    f.setup()
    try:
        assert os.path.isdir(f.rt.workdir), "setup 后 workdir 应存在"
        f.rt.cleanup()
        assert not os.path.isdir(f.rt.workdir), f"cleanup 后 workdir 应消失: {f.rt.workdir}"
        # 幂等：再次 cleanup 不报错
        f.rt.cleanup()
        print("✅ test_cleanup_removes_workdir 通过")
    finally:
        f.teardown()


# —— 14) key= 参数：同工具多次调用不撞缓存（P1-3 真实 Coder 用例）—— #

def test_tool_key_disambiguates_multiple_calls():
    """在同 node + step 内对同一工具调多次，每次传 key= 应独立缓存。

    这是 P1-3 真实 Coder 的硬性要求：每个 task 写一个文件，不能撞缓存。
    """
    f = _ToolFixture()
    f.setup()
    try:
        # 准备两个文件
        path_a = os.path.join(f.rt.workdir, "a.txt")
        path_b = os.path.join(f.rt.workdir, "b.txt")
        f.rt.write_file("a.txt", "content A")
        f.rt.write_file("b.txt", "content B")

        # 构造一个图，节点内对 read_file 调两次，key= 不同
        g = StateGraph(StateSchema())
        def reader(state, ctx):
            # 两次 read_file 调同一工具名 path 不同，需要独立缓存
            # 不传 key= 时第二次会撞第一次的缓存（这里我们验证有 key= 时不撞）
            content_a = ctx.tool("read_file", key="a", fn=lambda: f.rt.read_file("a.txt"))
            content_b = ctx.tool("read_file", key="b", fn=lambda: f.rt.read_file("b.txt"))
            return {"a": content_a, "b": content_b, "log": []}
        g.add_node("reader", reader)
        g.add_edge(START, "reader")
        g.add_edge("reader", END)
        app = g.compile(Checkpointer())

        r = app.invoke({}, thread_id="key_test")
        assert r.status == "completed"
        assert r.state["a"] == "content A", f"a 应是 content A，实际 {r.state['a']!r}"
        assert r.state["b"] == "content B", f"b 应是 content B，实际 {r.state['b']!r}"
        print("✅ test_tool_key_disambiguates_multiple_calls 通过")
    finally:
        f.teardown()


# —— 15) CR 2026-06-17 1.3: 同 key + 不同 fn 撞缓存 —— #

def test_tool_key_collision_known_behavior():
    """同 key + 不同 fn：第二次命中第一次的缓存（这是有意行为，仅文档化）。"""
    f = _ToolFixture()
    f.setup()
    try:
        f.rt.write_file("a.txt", "content A")
        f.rt.write_file("b.txt", "content B")
        g = StateGraph(StateSchema())
        call_count = {"a": 0, "b": 0}
        def reader(state, ctx):
            # 同 key="x" 给两个不同文件
            r1 = ctx.tool("read_file", key="x", fn=lambda: (call_count.__setitem__("a", call_count["a"]+1) or f.rt.read_file("a.txt")))
            r2 = ctx.tool("read_file", key="x", fn=lambda: (call_count.__setitem__("b", call_count["b"]+1) or f.rt.read_file("b.txt")))
            return {"a": r1, "b": r2, "log": []}
        g.add_node("reader", reader)
        g.add_edge(START, "reader")
        g.add_edge("reader", END)
        app = g.compile(Checkpointer())
        r = app.invoke({}, thread_id="collision_test")
        # 第二次调用因为 key 撞缓存，fn 不执行，直接返回第一次结果
        assert r.state["a"] == "content A"
        assert r.state["b"] == "content A", f"撞缓存：b 应返回 a 的结果，实际 {r.state['b']!r}"
        assert call_count["a"] == 1
        assert call_count["b"] == 0, f"撞缓存后第二次 fn 不应执行，实际 call_count['b']={call_count['b']}"
        print("✅ test_tool_key_collision_known_behavior 通过（同 key 撞缓存是已知行为）")
    finally:
        f.teardown()


# —— 16) CR 2026-06-17 2.4: ctx.tool() 未知 kwargs 打印 WARN —— #

def test_tool_warns_on_unknown_kwargs():
    """传未知 kwargs 应打 WARN，不静默忽略。"""
    f = _ToolFixture()
    f.setup()
    try:
        f.rt.write_file("a.txt", "x")
        g = StateGraph(StateSchema())
        def reader(state, ctx):
            # 故意 typo: input_sumary（漏字母 m）
            content = ctx.tool("read_file", fn=lambda: f.rt.read_file("a.txt"), input_sumary="oops")
            return {"content": content}
        g.add_node("reader", reader)
        g.add_edge(START, "reader")
        g.add_edge("reader", END)
        app = g.compile(Checkpointer())
        # 捕获 stdout
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = app.invoke({}, thread_id="kwarg_test")
        output = buf.getvalue()
        assert "WARN" in output and "input_sumary" in output, f"应打 WARN 含 typo key，实际输出: {output!r}"
        assert r.status == "completed"
        print("✅ test_tool_warns_on_unknown_kwargs 通过")
    finally:
        f.teardown()


# —— 17) CR 2026-06-17 1.1+1.2: run_cmd 沙箱强化 —— #

def test_run_cmd_rejects_python3():
    """白名单移除 python/python3 后，`python3 -c '...'` 应被拒绝。"""
    f = _ToolFixture()
    f.setup()
    try:
        try:
            f.rt.run_cmd("python3 -c 'print(1)'")
            assert False, "应拒绝 python3 但接受了"
        except PermissionError as e:
            assert "python3" in str(e) or "白名单" in str(e), f"错误信息应提及 python3/白名单: {e}"
        print("✅ test_run_cmd_rejects_python3 通过")
    finally:
        f.teardown()


def test_run_cmd_cat_rejects_absolute_path():
    """`cat /etc/passwd` 应被 workdir 路径校验拒绝。"""
    f = _ToolFixture()
    f.setup()
    try:
        try:
            f.rt.run_cmd("cat /etc/passwd")
            assert False, "应拒绝绝对路径但接受了"
        except PermissionError as e:
            assert "绝对路径" in str(e) or "workdir" in str(e), f"错误信息应提 workdir: {e}"
        print("✅ test_run_cmd_cat_rejects_absolute_path 通过")
    finally:
        f.teardown()


def test_run_cmd_cat_accepts_relative_path():
    """`cat a.txt`（相对路径）应正常执行。"""
    f = _ToolFixture()
    f.setup()
    try:
        f.rt.write_file("a.txt", "hello\n")
        out = f.rt.run_cmd("cat a.txt")
        assert out["exit_code"] == 0
        assert "hello" in out["stdout"]
        print("✅ test_run_cmd_cat_accepts_relative_path 通过")
    finally:
        f.teardown()


# —— 18) CR 2026-06-17 2.1: apply_patch 空 diff 拒绝 —— #

def test_apply_patch_rejects_empty_diff():
    """空 unified_diff 应抛 ValueError，不静默创建空文件。"""
    f = _ToolFixture()
    f.setup()
    try:
        for empty in ("", "   ", "\n\n"):
            try:
                f.rt.apply_patch("evil.py", empty)
                assert False, f"应拒绝空 diff {empty!r} 但接受了"
            except ValueError as e:
                assert "非空" in str(e), f"错误信息应提'非空': {e}"
        # 验证文件确实没创建
        assert not os.path.exists(os.path.join(f.rt.workdir, "evil.py")), \
            "空 diff 不应创建文件"
        print("✅ test_apply_patch_rejects_empty_diff 通过")
    finally:
        f.teardown()


# —— 13) git_diff 在非 git 仓库中返回 "" —— #

def test_git_diff_in_non_git_repo():
    f = _ToolFixture()
    f.setup()
    try:
        # workdir 是新建空目录，.git 不存在
        out = f.rt.git_diff("HEAD")
        assert out == "", f"非 git 仓库应返回空字符串，实际 {out!r}"
        # 两次 ref 也不报错
        out2 = f.rt.git_diff("HEAD", "HEAD")
        assert out2 == ""
        print("✅ test_git_diff_in_non_git_repo 通过")
    finally:
        f.teardown()


# —— 入口 —— #

def main() -> int:
    tests = [
        test_read_file_existing,
        test_read_file_missing,
        test_write_file_new_path,
        test_write_file_overwrite,
        test_apply_patch_valid,
        test_apply_patch_invalid,
        test_run_cmd_success,
        test_run_cmd_nonzero_exit,
        test_run_cmd_rejects_dotdot,
        test_run_cmd_timeout,
        test_tool_caching,
        test_cleanup_removes_workdir,
        test_tool_key_disambiguates_multiple_calls,
        test_tool_key_collision_known_behavior,
        test_tool_warns_on_unknown_kwargs,
        test_run_cmd_rejects_python3,
        test_run_cmd_cat_rejects_absolute_path,
        test_run_cmd_cat_accepts_relative_path,
        test_apply_patch_rejects_empty_diff,
        test_git_diff_in_non_git_repo,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"❌ {t.__name__} 失败: {type(e).__name__}: {e}")
    total = len(tests)
    if failed:
        print(f"\n❌ {failed}/{total} 失败")
        return 1
    print(f"\n✅ 全部 {total} 个 ToolRuntime 测试通过\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
