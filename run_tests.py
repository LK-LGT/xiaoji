#!/usr/bin/env python
"""快速测试运行脚本"""

import sys
import subprocess

result = subprocess.run(
    [sys.executable, "-m", "pytest", 
     "backend/app/tests/test_create_assessment_task.py", 
     "-v", "--tb=short"],
    cwd="."
)

sys.exit(result.returncode)
