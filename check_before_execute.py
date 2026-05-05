#!/usr/bin/env python3
"""
执行前记忆检查 CLI 入口
委托给 memory_api.check_before_execute
"""
import sys
from pathlib import Path

from memory_consts import SKILL_DIR
sys.path.insert(0, str(SKILL_DIR))

from memory_api import check_before_execute

if __name__ == '__main__':
    if len(sys.argv) > 1:
        check_before_execute(' '.join(sys.argv[1:]))
    else:
        print("用法: python3 check_before_execute.py <命令>")
        print("示例: python3 check_before_execute.py npm install xxx")
