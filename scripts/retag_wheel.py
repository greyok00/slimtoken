#!/usr/bin/env python3
"""Retag the slimtoken wheel to the actual binary platform.

Problem (audit F5): `python -m build` produces slimtoken-<v>-py3-none-any.whl
even though the package ships CPython-3.13 x86-64 .so modules, because the .so
files are included as package data, not ext_modules. pip will therefore
install this wheel on any Python >= 3.13, any OS, and the imports then fail.

Fix: derive the tag from the shipped .so filenames (must all be identical),
patch dist-info/WHEEL, and rename the wheel. Fails loudly if no binaries or
mixed binary tags are present — no silent "correction".

Usage: retag_wheel.py <wheel> [--dry-run]
"""
import sys, zipfile, re, shutil, os
from pathlib import Path

def so_tags(names):
    """Derive the wheel tag from .so filenames.

    `mod.cpython-313-x86_64-linux-gnu.so` is the CPython ext filename spelling:
    interpreter 3.13, arch x86_64, sysname linux, libc gnu. The wheel platform
    tag spells it '<sysname>_<arch>' with the libc dropped, so the wheel tag is
    `cp313-cp313-linux_x86_64`."""
    tags = set()
    for n in names:
        m = re.match(r".*\.cpython-(\d{2,3})-([^-]+)-([^-]+)-([^-]+)\.so$", n)
        if not m:
            continue
        ver, arch, sysname, libc = m.groups()
        tags.add("cp%s-cp%s-%s_%s" % (ver, ver, sysname, arch))
    return tags

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        print("usage: retag_wheel.py <wheel> [--dry-run]"); return 2
    wheel = Path(args[0])
    z = zipfile.ZipFile(wheel)
    names = z.namelist()
    tags = so_tags(names)
    if len(tags) != 1:
        print("FAIL: expected exactly one .so platform tag in %s, found %s" % (wheel.name, tags or "none")); return 1
    tag = tags.pop()
    wheelmeta = z.read([n for n in names if n.endswith(".dist-info/WHEEL")][0]).decode()
    if tag in wheelmeta:
        print("already tagged correctly: %s" % wheel.name); return 0
    out = wheel.with_name("%s-%s%s" % (wheel.name.rsplit("-", 3)[0], tag.lower(), ".whl")
                          if False else wheel.name.replace("py3-none-any", tag.lower()))
    fixed = wheelmeta.replace("Tag: py3-none-any", "Tag: %s" % tag.lower())
    if fixed == wheelmeta:
        print("FAIL: WHEEL metadata had no py3-none-any tag to replace"); return 1
    if dry:
        print("DRY-RUN: would write %s" % out.name); return 0
    tmp = out.with_suffix(".tmp")
    record_name = [n for n in names if n.endswith(".dist-info/RECORD")][0]
    record = z.read(record_name).decode()
    new_record = []
    for line in record.splitlines():
        if not line.strip():
            continue
        path, h, size = (line.split(",") + ["", ""])[:3]
        if path.endswith(".dist-info/WHEEL"):
            import base64, hashlib
            data = fixed.encode()
            h = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            size = str(len(data))
            new_record.append(",".join([path, h, size]))
        else:
            new_record.append(line)
    fixed_record = "\n".join(new_record) + "\n"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in z.infolist():
            data = z.read(item.filename)
            if item.filename.endswith(".dist-info/WHEEL"):
                data = fixed.encode()
            elif item.filename == record_name:
                data = fixed_record.encode()
            zo.writestr(item, data)
    z.close()
    tmp.replace(out)
    print("retagged: %s -> %s (%s)" % (wheel.name, out.name, tag.lower()))
    os.remove(wheel)
    return 0

if __name__ == "__main__":
    sys.exit(main())