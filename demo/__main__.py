"""Run all demo scenarios."""
from __future__ import annotations

from .demo_dynamic_send import run_dynamic_send
from .demo_llm_config import run_llm_config
from .demo_parallel import run_parallel
from .demo_pipeline import run_pipeline
from .demo_real_coder import run_real_coder
from .demo_real_debugger import run_real_debugger
from .demo_retry import run_retry
from .demo_timetravel import run_timetravel


def main() -> None:
    run_pipeline()
    run_parallel()
    run_retry()
    run_timetravel()
    run_llm_config()
    run_real_coder()
    run_real_debugger()
    run_dynamic_send()
    print("\n✅ 全部场景执行完毕\n")


if __name__ == "__main__":
    main()
