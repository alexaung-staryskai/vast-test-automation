"""Fake SSH server for trying the consoles without real clients.
Nothing is executed on this machine: commands are echoed, and elbencho
commands print a fake result table.

Run:  .venv/bin/python dev/mock_ssh.py [port]     (default 127.0.0.1:2222, password "test")
Start one per mock client (e.g. 2222 and 2223) - like real hosts.
"""
import asyncio
import re
import random
import shlex
from pathlib import Path

import asyncssh

KEY = Path(__file__).with_name(".mock_host_key")
INSTALLED = {"nano", "nfs-utils"}  # fio and elbencho start "missing"
UPGRADED: set[str] = set()


class Server(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return password == "test"


def fake_elbencho(cmd: str) -> str:
    op = "WRITE" if " -w" in cmd else "READ" if " -r" in cmd else "RMFILES"
    mib = random.randint(3000, 6000)
    t = round(random.uniform(8, 12), 3)
    return (
        "OPERATION   RESULT TYPE         FIRST DONE   LAST DONE\n"
        "=========== ================    ==========   =========\n"
        f"{op:<11} Elapsed time     :     {t}s     {t + 0.4:.3f}s\n"
        f"            IOPS             :      {mib}       {mib - 40}\n"
        f"            Throughput MiB/s :      {mib}       {mib - 40}\n"
        f"            Total MiB        :      {mib * 10}      {mib * 10 + 300}\n"
        + ("            IO latency       : [ min=420us avg=1.45ms max=26.3ms ]\n"
           f"            IO lat % us      : [ 1%<=560 50%<=1216 75%<=1904 99%<={random.randint(3400, 3900)} 99.9%<=4928 ]\n"
           if "--lat" in cmd else "")
        + "---\n"
    )


async def readline(process) -> str:
    while True:
        try:
            return await process.stdin.readline()
        except asyncssh.TerminalSizeChanged:
            continue


async def handle(process: asyncssh.SSHServerProcess):
    user = process.get_extra_info("username")
    if process.command is None:  # interactive shell
        prompt = f"{user}@mock-client:~$ "
        process.stdout.write(f"Welcome to the mock client ({user}). Commands are only echoed.\r\n{prompt}")
        line = ""
        try:
            while True:
                try:
                    ch = await process.stdin.read(1)
                except asyncssh.TerminalSizeChanged:
                    continue  # the browser terminal was resized
                if not ch:
                    break
                if ch in ("\r", "\n"):
                    process.stdout.write("\r\n")
                    if line.strip() == "exit":
                        break
                    if line.strip():
                        process.stdout.write(f"[mock] {line}\r\n")
                    process.stdout.write(prompt)
                    line = ""
                elif ch == "\x7f":
                    if line:
                        line = line[:-1]
                        process.stdout.write("\b \b")
                elif ch == "\x03":
                    process.stdout.write("^C\r\n" + prompt)
                    line = ""
                else:
                    line += ch
                    process.stdout.write(ch)
        except asyncssh.BreakReceived:
            pass
        process.exit(0)
        return

    cmd = process.command
    if cmd.strip() == "sudo -n true":  # mock users need a sudo password
        process.exit(1)
        return
    if cmd.startswith("sudo -S"):
        pw = await readline(process)
        if pw.strip() != "test":
            process.stdout.write("sudo: incorrect password\n")
            process.exit(1)
            return
        cmd = shlex.split(cmd)[-1]
        process.stdout.write("[mock] (via sudo)\n")
    await asyncio.sleep(0.3)
    if "@@PM" in cmd:  # package check script
        process.stdout.write("@@PM|apt\n")
        versions = {"nano": "GNU nano, version 6.2", "fio": "fio-3.28", "nfs-utils": "mount.nfs present",
                    "elbencho": "elbencho 3.0.19"}
        # packages.system entries: everything the script asks about that the mock has no version for
        system = [n for n in re.findall(r"@@PKG\|([^|$]+)\|", cmd) if n not in versions]
        for name in system:
            versions[name] = "1.0-1"
        for name, ver in versions.items():
            if name in INSTALLED:
                process.stdout.write(f"@@PKG|{name}|0|{ver}\n")
            elif name in system:
                process.stdout.write(f"@@PKG|{name}|1|not installed\n")
            else:
                process.stdout.write(f"@@PKG|{name}|127|bash: {name}: command not found\n")
        if "nano" not in UPGRADED:
            process.stdout.write("@@UPG|nano/jammy-updates 6.2-1ubuntu0.1 amd64 [upgradable from: 6.2-1]\n")
        process.exit(0)
        return
    if "apt-get install" in cmd:
        # simulate a question so the app's "waiting for input" path is exercised
        process.stdout.write("Reading package lists... Done\nDo you want to continue? [Y/n] ")
        ans = (await readline(process)).strip().lower()
        if ans not in ("", "y", "yes"):
            process.stdout.write("Abort.\n")
            process.exit(1)
            return
        names = cmd.split("install -y", 1)[-1].replace("--only-upgrade", "").replace("--reinstall", "").split()
        for n in names:
            INSTALLED.add("nfs-utils" if n == "nfs-common" else n)
            UPGRADED.add(n)
        process.stdout.write(f"[mock] installed/updated: {' '.join(names)}\n")
        process.exit(0)
        return
    if "apt-get remove -y" in cmd or "apt-get purge -y" in cmd:
        names = cmd.split(" -y", 1)[-1].split()
        for n in names:
            INSTALLED.discard("nfs-utils" if n == "nfs-common" else n)
        process.stdout.write(f"[mock] removed: {' '.join(names)}\n")
        process.exit(0)
        return
    if "command -v elbencho" in cmd and "rm -f" in cmd:   # elbencho uninstall
        INSTALLED.discard("elbencho")
        process.stdout.write("removed /usr/local/bin/elbencho\n")
        process.exit(0)
        return
    if "elbencho.tar.gz" in cmd:
        INSTALLED.add("elbencho")
        process.stdout.write("[mock] downloaded elbencho static build\nelbencho 3.0.19\n")
        process.exit(0)
        return
    if cmd.lstrip().startswith("elbencho") and "--version" not in cmd:
        await asyncio.sleep(1)
        process.stdout.write(fake_elbencho(cmd))
    elif cmd.startswith("hostname; uname -sr"):   # 🔌 Test connection
        process.stdout.write(f"{user}-mock-client\nLinux 6.8.0-49-generic\nUbuntu 24.04.1 LTS\n")
    elif "fail" in cmd:
        process.stdout.write("[mock] simulated failure\n")
        process.exit(2)
        return
    else:
        process.stdout.write(f"[mock] ran: {cmd}\n")
    process.exit(0)


async def main():
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 2222
    if not KEY.exists():
        asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(KEY))
    await asyncssh.create_server(Server, "127.0.0.1", port, server_host_keys=[str(KEY)], process_factory=handle)
    print(f"mock SSH on 127.0.0.1:{port} (password: test)", flush=True)
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
