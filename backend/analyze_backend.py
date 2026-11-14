#!/usr/bin/env python3
import os
from pathlib import Path

root = 'app'
modules = {}

for dirpath, dirnames, filenames in os.walk(root):
    for filename in filenames:
        if filename.endswith('.py'):
            filepath = os.path.join(dirpath, filename)
            lines = len(open(filepath, encoding='utf-8').readlines())
            
            # 按一级目录分类
            parts = dirpath.split(os.sep)
            if len(parts) > 1:
                module = parts[1]
            else:
                module = 'root'
            
            if module not in modules:
                modules[module] = {'count': 0, 'lines': 0, 'files': []}
            
            modules[module]['count'] += 1
            modules[module]['lines'] += lines
            modules[module]['files'].append((filename, lines))

print('Backend 模块详情:')
print('=' * 80)
for module in sorted(modules.keys()):
    data = modules[module]
    print(f'\n📁 {module.upper()}')
    print(f'   文件数: {data["count"]}, 行数: {data["lines"]}')
    # 显示最大的几个文件
    top_files = sorted(data['files'], key=lambda x: x[1], reverse=True)[:3]
    for fname, lines in top_files:
        print(f'   - {fname}: {lines} 行')
