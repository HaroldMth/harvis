#!/usr/bin/env python3
import socket
import sys

def main():
    socket_path = "/tmp/byteai.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(socket_path)
            s.sendall(b"trigger")
            print("Trigger sent to BYTE AI.")
    except Exception as e:
        with open("/tmp/trigger_error.log", "w") as f:
            f.write(f"Error: {e}\n")
        print(f"Failed to trigger BYTE AI. Is it running? Error: {e}", file=sys.stderr)
        sys.exit(1)
        
    with open("/tmp/trigger_success.log", "w") as f:
        f.write("Trigger executed successfully.\n")

if __name__ == "__main__":
    main()
