#!/usr/bin/env python3
"""
Quick test script to verify web chat server installation
"""

import sys
import os

def check_dependencies():
    """Check if all required packages are installed"""
    required = ['fastapi', 'uvicorn', 'websockets', 'aiomqtt', 'pydantic']
    missing = []

    print("Checking dependencies...")
    for package in required:
        try:
            __import__(package)
            print(f"  ✓ {package}")
        except ImportError:
            print(f"  ✗ {package} - MISSING")
            missing.append(package)

    if missing:
        print(f"\nMissing packages: {', '.join(missing)}")
        print("Install with: pip3 install -r requirements.txt")
        return False

    return True


def check_files():
    """Check if all required files exist"""
    files = [
        'server.py',
        'index.html',
        'static/styles.css',
        'static/app.js',
        'requirements.txt'
    ]

    print("\nChecking files...")
    missing = []

    for file in files:
        path = os.path.join(os.path.dirname(__file__), file)
        if os.path.exists(path):
            print(f"  ✓ {file}")
        else:
            print(f"  ✗ {file} - MISSING")
            missing.append(file)

    if missing:
        print(f"\nMissing files: {', '.join(missing)}")
        return False

    return True


def test_import_server():
    """Try to import the server module"""
    print("\nTesting server import...")
    try:
        sys.path.insert(0, os.path.dirname(__file__))
        import server
        print("  ✓ Server module imported successfully")
        return True
    except Exception as e:
        print(f"  ✗ Failed to import server: {e}")
        return False


def check_port():
    """Check if port 3001 is available"""
    print("\nChecking port 3001...")
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', 3001))
    sock.close()

    if result == 0:
        print("  ⚠ Port 3001 is already in use")
        print("    Stop existing service: sudo systemctl stop web_chat")
        return False
    else:
        print("  ✓ Port 3001 is available")
        return True


def main():
    print("=" * 60)
    print("SENTIENT CORE WEB CHAT - INSTALLATION TEST")
    print("=" * 60)

    checks = [
        check_dependencies(),
        check_files(),
        test_import_server(),
        check_port()
    ]

    print("\n" + "=" * 60)
    if all(checks):
        print("✓ ALL CHECKS PASSED")
        print("\nReady to start server:")
        print("  python3 server.py")
        print("\nOr install as service:")
        print("  sudo cp web_chat.service /etc/systemd/system/")
        print("  sudo systemctl daemon-reload")
        print("  sudo systemctl enable web_chat")
        print("  sudo systemctl start web_chat")
        return 0
    else:
        print("✗ SOME CHECKS FAILED")
        print("\nPlease fix the issues above before starting the server.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
