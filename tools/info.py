import os
import shutil
import subprocess


def _top_processes(sort_key: str, n: int = 8) -> str:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,user,pcpu,pmem,comm", f"--sort=-{sort_key}", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()][:n]
        if not lines:
            return "N/A"
        return "\n".join(f"  {ln}" for ln in lines)
    except Exception as e:
        return str(e)


def system_info():
    """
    Returns CPU load, RAM, disk usage, and top CPU/memory processes.
    """
    try:
        # Load average (CPU)
        if os.path.exists("/proc/loadavg"):
            with open("/proc/loadavg", "r") as f:
                load = f.read().strip().split()
                cpu_load = f"1m: {load[0]}, 5m: {load[1]}, 15m: {load[2]}"
        else:
            cpu_load = "N/A"

        # Memory usage
        mem_total = "N/A"
        mem_free = "N/A"
        mem_available = "N/A"
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_total = line.split()[1]
                    elif line.startswith("MemFree:"):
                        mem_free = line.split()[1]
                    elif line.startswith("MemAvailable:"):
                        mem_available = line.split()[1]
            try:
                total_gb = round(float(mem_total) / 1024 / 1024, 2)
                avail_gb = round(float(mem_available) / 1024 / 1024, 2)
                mem_str = f"Total: {total_gb} GB, Available: {avail_gb} GB"
            except ValueError:
                mem_str = f"Total: {mem_total} kB, Free: {mem_free} kB"
        else:
            mem_str = "N/A"

        # Disk usage
        total, used, free = shutil.disk_usage("/")
        total_gb = round(total / (2**30), 2)
        used_gb = round(used / (2**30), 2)
        free_gb = round(free / (2**30), 2)
        disk_str = f"Total: {total_gb} GB, Used: {used_gb} GB, Free: {free_gb} GB"

        top_cpu = _top_processes("pcpu")
        top_mem = _top_processes("pmem")
        output_str = (
            f"CPU Load: {cpu_load}\nMemory: {mem_str}\nDisk: {disk_str}\n"
            f"Top CPU (pid user cpu% mem% command):\n{top_cpu}\n"
            f"Top RAM (pid user cpu% mem% command):\n{top_mem}"
        )
        return {
            "success": True,
            "output": output_str,
            "cpu_load": cpu_load,
            "memory": mem_str,
            "disk": disk_str
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
