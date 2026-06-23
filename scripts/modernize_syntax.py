#!/usr/bin/env python3
"""Python 3.14 syntax modernization bulk transformer.

Applies mechanical transformations across the codebase:
1. Remove `from __future__ import annotations`
2. Replace typing.Dict/List/Tuple/Optional/Union with builtins / PEP 604
3. Replace f-string debug patterns (print/log) with `{var=}` syntax where safe
4. Update README.md version references

This script handles the bulk work. Manual follow-up needed for:
- match/case refactoring in graph.py
- walrus operator insertion
- test_py314_compat.py creation
- verify_py314.sh creation
"""

from __future__ import annotations

import re
from pathlib import Path


# Files to transform
AGENTFLOW_FILES = [
    "agentflow/graph.py",
    "agentflow/nodes.py",
    "agentflow/tools.py",
    "agentflow/checkpoint.py",
    "agentflow/state.py",
    "agentflow/interrupt.py",
    "agentflow/llm.py",
    "agentflow/plan.py",
    "agentflow/graph_config.py",
]

TEST_FILES = [
    "test/test_py37_compat.py",  # will be replaced entirely later
    "test/test_activity.py",
    "test/test_invariants.py",
    "test/test_graph_config.py",
    "test/test_planner.py",
    "test/test_send.py",
    "test/test_subgraph.py",
    "test/test_debugger.py",
    "test/test_tools.py",
    "test/test_graph.py",
    "test/test_mcp.py",
    "test/test_coder.py",
    "test/test_review.py",
    "test/cr_fuzz_subgraph.py",
]

DEMO_FILES = [
    "demo.py",
    "demo/demo_real_debugger.py",
    "demo/demo_retry.py",
    "demo/demo_real_coder.py",
    "demo/demo_subgraph.py",
    "demo/demo_parallel.py",
    "demo/common.py",
    "demo/demo_timetravel.py",
    "demo/demo_llm_config.py",
    "demo/demo_dynamic_send.py",
    "demo/__main__.py",
    "demo/demo_pipeline.py",
]

ALL_FILES = AGENTFLOW_FILES + TEST_FILES + DEMO_FILES


def remove_future_annotations(content: str) -> str:
    """Remove `from __future__ import annotations` line."""
    lines = content.splitlines()
    new_lines = []
    for line in lines:
        if line.strip() == "from __future__ import annotations":
            continue
        new_lines.append(line)
    return "\n".join(new_lines)


def modernize_typing_imports(content: str) -> str:
    """Replace old typing imports with modern equivalents.
    
    - Dict -> dict
    - List -> list  
    - Tuple -> tuple
    - Optional[X] -> X | None
    - Union[X, Y] -> X | Y
    - Clean up unused typing imports
    """
    # First, remove from __future__ import annotations
    content = remove_future_annotations(content)
    
    # Handle the typing import line(s)
    # Pattern: from typing import Any, Dict, List, Optional, ...
    # We need to:
    # 1. Remove Dict, List, Tuple, Optional, Union from imports
    # 2. If the import becomes empty or just "from typing import", handle gracefully
    
    lines = content.splitlines()
    new_lines = []
    
    for line in lines:
        # Match typing import lines
        if re.match(r'^from typing import ', line):
            # Parse what's imported
            imports_str = line[len('from typing import '):]
            imports = [i.strip() for i in imports_str.split(',')]
            
            # Remove old-style types that have builtin equivalents
            old_types = {'Dict', 'List', 'Tuple', 'Optional', 'Union'}
            new_imports = [i for i in imports if i not in old_types]
            
            if not new_imports:
                # Skip empty import line
                continue
            
            # Reconstruct the import line
            new_line = 'from typing import ' + ', '.join(new_imports)
            new_lines.append(new_line)
        else:
            new_lines.append(line)
    
    content = '\n'.join(new_lines)
    
    # Now replace type annotations in the rest of the file
    # Dict[...] -> dict[...]
    content = re.sub(r'\bDict\[', 'dict[', content)
    content = re.sub(r'\]\s*->\s*Dict', '] -> dict', content)
    content = re.sub(r'\bDict\b', 'dict', content)
    
    # List[...] -> list[...]
    content = re.sub(r'\bList\[', 'list[', content)
    content = re.sub(r'\]\s*->\s*List', '] -> list', content)
    content = re.sub(r'\bList\b', 'list', content)
    
    # Tuple[...] -> tuple[...]
    content = re.sub(r'\bTuple\[', 'tuple[', content)
    content = re.sub(r'\]\s*->\s*Tuple', '] -> tuple', content)
    content = re.sub(r'\bTuple\b', 'tuple', content)
    
    # Optional[X] -> X | None
    content = re.sub(r'\bOptional\[([^\]]+)\]', r'\1 | None', content)
    
    # Union[X, Y] -> X | Y (handle nested cases)
    # Simple case: Union[A, B] -> A | B
    content = re.sub(r'\bUnion\[([^,\[\]]+),\s*([^\[\]]+)\]', r'\1 | \2', content)
    # More complex: Union[A, B, C] -> A | B | C
    def replace_union(match):
        inner = match.group(1)
        parts = [p.strip() for p in inner.split(',')]
        return ' | '.join(parts)
    content = re.sub(r'\bUnion\[([^\]]+)\]', replace_union, content)
    
    return content


def modernize_fstring_debug(content: str) -> str:
    """Convert f-string debug patterns to use = suffix where safe.
    
    E.g., f"key={value}" -> f"{value=}"
    But be careful not to change f-strings that are not debug output.
    """
    # This is tricky - we should only change debug/log print statements
    # For safety, let's skip this automated step and do it manually
    return content


def update_readme(content: str) -> str:
    """Update README.md version references."""
    content = content.replace('Python 3.7+', 'Python 3.12+')
    content = content.replace('Python 3.7', 'Python 3.12')
    content = content.replace('py37', 'py312')
    content = content.replace('3.7', '3.12')
    return content


def process_file(path: Path, do_readme: bool = False) -> bool:
    """Process a single file. Returns True if changed."""
    try:
        original = path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  SKIP {path}: {e}")
        return False
    
    content = original
    
    if do_readme and path.name == 'README.md':
        content = update_readme(content)
    else:
        content = modernize_typing_imports(content)
        # Skip f-string debug automation for safety
        # content = modernize_fstring_debug(content)
    
    if content == original:
        return False
    
    path.write_text(content, encoding='utf-8')
    return True


def main():
    root = Path('.')
    changed = 0
    skipped = 0
    
    print("Phase 1: Modernize typing imports + remove __future__ annotations")
    print("=" * 60)
    
    for rel_path in ALL_FILES:
        path = root / rel_path
        if not path.exists():
            print(f"  MISSING {rel_path}")
            skipped += 1
            continue
        
        is_readme = path.name == 'README.md'
        if process_file(path, do_readme=is_readme):
            print(f"  MOD   {rel_path}")
            changed += 1
        else:
            print(f"  OK    {rel_path}")
    
    print(f"\nChanged: {changed}, Skipped/Missing: {skipped}")
    print("\nNext steps (manual):")
    print("1. Apply match/case refactoring in agentflow/graph.py")
    print("2. Apply walrus operator insertions (3-5 locations)")
    print("3. Apply f-string = debug syntax manually")
    print("4. Create test/test_py314_compat.py")
    print("5. Create scripts/verify_py314.sh")
    print("6. Write docs/plan-py314-syntax.md")
    print("7. Run tests with Python 3.14")


if __name__ == '__main__':
    main()
