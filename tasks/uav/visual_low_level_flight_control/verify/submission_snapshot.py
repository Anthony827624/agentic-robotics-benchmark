"""Freeze agent-owned regular files before executing any submitted code."""
import hashlib
import os
from pathlib import Path
import stat

MAX_BYTES = 128 * 1024 * 1024
MAX_FILES = 4096


def snapshot_submission(source, destination, byte_limit=MAX_BYTES):
    source, destination = Path(source), Path(destination)
    nofollow = os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    owner = os.fstat(root_fd).st_uid
    total, count = 0, 0
    digest = hashlib.sha256()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)

    def copy_directory(fd, target, relative, depth):
        nonlocal total, count
        if depth > 16:
            raise ValueError("submission directory nesting exceeds limit")
        with os.scandir(fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            count += 1
            if count > MAX_FILES:
                raise ValueError("too many submission entries")
            path = relative/name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if info.st_uid != owner or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError("submission must contain only agent-owned regular files/directories")
            flags = os.O_RDONLY | nofollow | os.O_NONBLOCK
            if stat.S_ISDIR(info.st_mode):
                flags |= os.O_DIRECTORY
            child_fd = os.open(name, flags, dir_fd=fd)
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError("submission changed while being frozen")
                if stat.S_ISDIR(info.st_mode):
                    (target/name).mkdir(mode=0o700)
                    copy_directory(child_fd, target/name, path, depth+1)
                else:
                    digest.update(str(path).encode()+b"\0")
                    with (target/name).open("xb") as output:
                        while chunk := os.read(child_fd, 65536):
                            total += len(chunk)
                            if total > byte_limit:
                                raise ValueError("submission exceeds size limit")
                            output.write(chunk)
                            digest.update(chunk)
                    digest.update(b"\0")
                    (target/name).chmod(0o555 if info.st_mode & 0o111 else 0o444)
            finally:
                os.close(child_fd)
        target.chmod(0o555)

    try:
        copy_directory(root_fd, destination, Path(), 0)
    finally:
        os.close(root_fd)
    return {"sha256": digest.hexdigest(), "bytes": total, "entries": count}
