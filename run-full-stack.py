#!/usr/bin/env python
"""启动完整的绩效管理系统（后端 + 前端）。

使用方法：
  python run-full-stack.py              # 启动后端 + 前端
  python run-full-stack.py --backend    # 仅启动后端
  python run-full-stack.py --frontend   # 仅启动前端
"""

import socket
import subprocess
import sys
from pathlib import Path


def lan_ips() -> list[str]:
    """列出本机可用的局域网 IPv4 地址。

    为什么需要：其他电脑访问本机服务时**不能**用 `localhost`
    （那指向访问者自己的电脑）。必须用本机的局域网 IP，
    否则必然连不上。这个地址要在启动时明确打印出来。
    """
    ips: set[str] = set()
    try:
        # 常规做法：连一个外网地址，拿到系统选用的出口网卡 IP。
        # 这里不发包，只做路由决策，不会真的产生网络流量。
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.2)
            sock.connect(("8.8.8.8", 80))
            ips.add(sock.getsockname()[0])
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass

    return sorted(ips)


def print_access_info(frontend_port: int = 5180, backend_port: int = 8000) -> None:
    """打印本机与局域网访问地址。"""
    print("\n" + "-"*80)
    print("🌐 访问地址")
    print("-"*80)
    print(f"  本机前端: http://localhost:{frontend_port}")
    print(f"  本机 API: http://localhost:{backend_port}/docs")

    ips = lan_ips()
    if not ips:
        print("\n  ⚠️ 未探测到局域网地址：请确认已连接网络（有线/Wi-Fi）。")
        print("     其它电脑请用「ipconfig」查到的 IPv4 地址访问。")
    else:
        print("\n  📡 其它电脑请用以下地址访问（不要用 localhost）：")
        for ip in ips:
            print(f"     前端: http://{ip}:{frontend_port}")
            print(f"     API:  http://{ip}:{backend_port}/docs")
    print("-"*80 + "\n")


def run_backend():
    """启动后端服务 (FastAPI)。"""
    print("\n" + "="*80)
    print("🚀 启动后端服务...")
    print("="*80)

    backend_dir = Path("backend")
    if not backend_dir.exists():
        print("❌ 后端目录不存在: backend")
        return False

    try:
        # 启动 FastAPI 服务
        cmd = [
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--host", "0.0.0.0",
            "--port", "8000",
            "--reload"
        ]

        print(f"执行命令: {' '.join(cmd)}")
        print(f"工作目录: {backend_dir}")
        print("\n📡 后端服务启动中... (http://localhost:8000)")
        print("   📚 API 文档: http://localhost:8000/docs")

        subprocess.run(cmd, cwd=backend_dir)
        return True
    except Exception as e:
        print(f"❌ 后端启动失败: {e}")
        return False


def run_frontend():
    """启动前端服务 (Vite)。"""
    print("\n" + "="*80)
    print("🎨 启动前端服务...")
    print("="*80)

    frontend_dir = Path("frontend")
    if not frontend_dir.exists():
        print("❌ 前端目录不存在: frontend")
        return False

    try:
        # 检查 node_modules
        if not (frontend_dir / "node_modules").exists():
            print("📦 依赖不存在，执行 npm install...")
            subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)

        # 启动 Vite 开发服务器
        cmd = ["npm", "run", "dev"]

        print(f"执行命令: {' '.join(cmd)}")
        print(f"工作目录: {frontend_dir}")
        print("\n🌐 前端服务启动中... (http://localhost:5180)")
        print_access_info()

        subprocess.run(cmd, cwd=frontend_dir)
        return True
    except Exception as e:
        print(f"❌ 前端启动失败: {e}")
        return False


def main():
    """主函数。"""
    import argparse

    parser = argparse.ArgumentParser(description="启动绩效管理系统")
    parser.add_argument("--backend", action="store_true", help="仅启动后端")
    parser.add_argument("--frontend", action="store_true", help="仅启动前端")

    args = parser.parse_args()

    if args.backend:
        # 仅启动后端
        run_backend()
    elif args.frontend:
        # 仅启动前端
        run_frontend()
    else:
        # 同时启动后端和前端
        print("\n" + "="*80)
        print("🚀 启动完整的绩效管理系统")
        print("="*80)
        print("\n本脚本将在两个终端中启动后端和前端服务")
        print("由于 Python subprocess 的限制，您需要手动在两个不同的终端中运行：")
        print("\n终端 1 (后端):")
        print("  cd framework/backend")
        print("  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
        print("\n终端 2 (前端):")
        print("  cd framework/frontend")
        print("  npm run dev")
        print("\n然后访问:")
        print("  🌐 前端: http://localhost:5180")
        print("  📡 后端 API 文档: http://localhost:8000/docs")
        print_access_info()
        print("或者直接运行:")
        print("  python run-full-stack.py --backend")
        print("  python run-full-stack.py --frontend")
        print("\n" + "="*80)


if __name__ == "__main__":
    main()
