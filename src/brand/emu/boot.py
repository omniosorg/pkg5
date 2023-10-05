#!/usr/bin/python3 -Es

# {{{ CDDL HEADER
#
# This file and its contents are supplied under the terms of the
# Common Development and Distribution License ("CDDL"), version 1.0.
# You may only use this file in accordance with the terms of version
# 1.0 of the CDDL.
#
# A full copy of the text of the CDDL should have accompanied this
# source. A copy of the CDDL is also available via the Internet at
# http://www.illumos.org/license/CDDL.
#
# }}}

# Copyright 2023 OmniOS Community Edition (OmniOSce) Association.

import getopt
import bootlib
from bootlib import (
    fatal,
    error,
    debug,
    info,
    warning,
    boolv,
    diskpath,
    expandopts,
    collapseopts,
)
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import ucred
from itertools import zip_longest
from pprint import pprint, pformat

QEMUROOT = "/opt/ooce/qemu"

# fmt: off

# Default values
opts = {
    'arch':             None,
    #'bootorder':        'cd',
    'cloud-init':       'off',
    'console':          'zconsole,id=console0',
    'cpu':              None,
    'diskif':           'virtio',
    'netif':            'virtio',
    'ram':              '1G',
    'rng':              'off',
    'rtc':              'base=utc',
    'uuid':             None,
    'vcpus':            '1',
    'vga':              'off',
    'vnc':              'off',
}

aliases = {
    #'bootorder': {
    #    'cd':           'path0,bootdisk,cdrom0',
    #    'dc':           'cdrom0,path0,bootdisk',
    #},
    'netif': {
        'virtio':       'virtio-net-pci',
    },
    'diskif': {
        'virtio':       'virtio-blk-pci',
    },
    'vnc': {
        'on':   'unix=/tmp/vm.vnc',
    }
}

# fmt: on

##############################################################################

testmode = False
jsonmode = False
name = None
xmlfile = None

uc = ucred.get(os.getpid())
if not uc.has_priv("Effective", "sys_config"):
    testmode = True


def usage(msg=None):
    print(
        """
boot [-t] [-x xml] <[-z] zone>
   -t   Test mode - just show what would be done
   -j   Output the computed zone data in JSON format
   -x   Path to zone's XML file
   -z   Name of zone
"""
    )

    if msg:
        print(msg)
    sys.exit(2)


try:
    cliopts, args = getopt.getopt(sys.argv[1:], "jtx:z:")
except getopt.GetoptError:
    usage()
for opt, arg in cliopts:
    if opt == "-t":
        testmode = True
    elif opt == "-j":
        jsonmode = True
        testmode = True
        bootlib.set_quiet(True)
    elif opt == "-x":
        xmlfile = arg
    elif opt == "-z":
        name = arg
    else:
        fatal(f"unhandled option, {opt}")

if not name and len(args):
    name = args.pop(0)

if len(args):
    usage("Unexpected arguments")

if not name:
    usage("No zone name supplied")

bootlib.log_stdout(logging.DEBUG)

if not xmlfile:
    xmlfile = f"/etc/zones/{name}.xml"

z = bootlib.Zone(xmlfile)

if z.name != name:
    fatal(f"Zone name {name} does not match XML file {z.name}")

if not testmode and not os.path.isdir(z.zoneroot):
    fatal(f"Could not find zone root {z.zoneroot}")

if not testmode:
    try:
        os.unlink(f"{z.zoneroot}/tmp/init.log")
    except:
        pass
    bootlib.log_file(f"{z.zonepath}/log/zone.log", logging.DEBUG)

info(f"Zone name: {name}")

##############################################################################

for tag in opts.keys():
    z.parseopt(tag, opts, aliases)

for a in ["arch", "cpu"]:
    if not opts[a]:
        fatal(f'The "{a}" attribute is required')

qemu = f'{QEMUROOT}/bin/qemu-system-{opts["arch"]}'
if not os.path.exists(qemu):
    fatal("{qemu} not found")

# UUID
uuid = opts["uuid"] if opts["uuid"] else z.uuid()
debug(f"Final uuid: {uuid}")

##############################################################################


def add_disk(path, boot=False, intf=None, media="disk", index=-1):
    global args

    if not intf:
        intf = opts["diskif"]

    if index < 0:
        index = add_disk.index
        add_disk.index += 1
    elif index > add_disk.index:
        add_disk.index = index

    if media == "cdrom":
        args.extend(["-cdrom", path])
    else:
        node = f"{media}{index}"
        path = diskpath(path)
        devstr = f"{intf},drive={node},serial={node}"
        # if boot:
        #    devstr += ',boot=on'
        args.extend(
            [
                "-blockdev",
                f"driver=host_device,filename={path},node-name={node},discard=unmap",
                "-device",
                devstr,
            ]
        )


add_disk.index = 0

##############################################################################

args = []

args.extend(["-name", name])

args.extend(
    [
        "-L",
        f"{QEMUROOT}/share/qemu",
        "-smp",
        opts["vcpus"],
        "-m",
        opts["ram"],
        "-rtc",
        opts["rtc"],
        "-pidfile",
        "/tmp/vm.pid",
        "-monitor",
        "unix:/tmp/vm.monitor,server,nowait,nodelay",
        "-cpu",
        opts["cpu"],
    ]
)

ser = uuid

if boolv(opts["cloud-init"], "cloud-init", ignore=True) is not False:
    if opts["cloud-init"].startswith(("http://", "https://")):
        ser = "ds=nocloud-net;s={};i={}".format(opts["cloud-init"], uuid)
    else:
        z.build_cloudinit_image(uuid, opts["cloud-init"], testmode)
        ser = f"ds=nocloud;i={uuid}"
        add_disk("/cloud-init.iso", boot=False, intf="ide", media="cdrom")

args.extend(
    [
        "-smbios",
        "type=1,manufacturer={},product={},version={},serial={},uuid={},family={}".format(
            "OmniOS", "OmniOS HVM", "1.0", ser, uuid, "Virtual Machine"
        ),
    ]
)

if uuid:
    args.extend(["-uuid", uuid])

# Console

args.extend(
    [
        "-chardev",
        opts["console"],
        "-serial",
        "chardev:console0",
    ]
)

# CDROM

for i, v in z.build_devlist("cdrom", 5):
    add_disk(v, boot=False, intf="ide", media="cdrom")

# If the disks are not using IDE, then reset their index as there is no need
# to leave room for the CDROM.
if opts["diskif"] != "ide":
    add_disk.index = 0

# Network

vlan = 0
for f in z.findall("./network[@physical]"):
    ifname = f.get("physical").strip()
    mac = bootlib.get_mac(ifname)
    if not mac:
        continue

    if opts["netif"] == "e1000":
        # Unlikely to be right yet
        args.extend(
            [
                "-net",
                "nic,name=net{2},model={0},macaddr={1}".format(
                    opts["netif"], mac, vlan
                ),
            ]
        )
    else:
        args.extend(["-device", f'{opts["netif"]},netdev=net{vlan},mac={mac}'])

    args.extend(
        [
            "-netdev",
            f"vnic,id=net{vlan},ifname={ifname}",
        ]
    )

    vlan += 1

# Bootdisk

try:
    bootdisk = z.findattr("bootdisk")
    add_disk(bootdisk.get("value").strip(), boot=True)
except:
    pass

# Additional Disks

for i, v in z.build_devlist("disk", 16):
    add_disk(v)

# Display

if boolv(opts["vga"], "vga", ignore=True) is False:
    args.append("-nographic")
elif boolv(opts["vnc"], "vnc", ignore=True) is False:
    args.extend(["-display", "none"])
else:
    args.extend(["-display", "vnc=0"])
    args.extend(["-vnc", opts["vnc"]])

# RNG

if boolv(opts["rng"], "rng"):
    args.extend(
        [
            "-object",
            "rng-builtin,id=random",
        ]
    )

# Extra options

for i, v in z.build_devlist("extra", 16):
    args.extend(shlex.split(v))

##############################################################################

debug(f"Final arguments:\n{qemu} {pformat(args)}")
info(qemu)
info("{0}".format(" ".join(map(lambda s: f'"{s}"' if " " in s else s, args))))


def writecfg(fh, arg, nl=True):
    end = "\n" if nl else " "
    if testmode:
        print(arg, end=end)
    else:
        fh.write(f"{arg}\n")


fh = None
if not testmode:
    try:
        fh = tempfile.NamedTemporaryFile(
            mode="w", dir=f"{z.zoneroot}/etc", prefix="emu.", delete=False
        )
    except Exception as e:
        fatal(f"Could not create temporary file: {e}")
    else:
        debug(f"Created temporary file at {fh.name}")

    try:
        os.unlink(f"{z.zoneroot}/qemu-system")
    except:
        pass

    os.symlink(qemu, f"{z.zoneroot}/qemu-system")

writecfg(fh, "#\n# Generated from zone configuration\n#")

for arg, narg in zip_longest(args, args[1:]):
    writecfg(fh, arg, not narg or narg.startswith("-"))

# if vncpassword:
#    writecfg(fh, f'pci.0.{VNC_SLOT}.0.password={vncpassword}')

if not testmode:
    tf = fh.name
    fh.close()
    try:
        os.rename(tf, f"{z.zoneroot}/etc/qemu.cfg")
    except Exception as e:
        fatal(f"Could not create qemu.cfg from temporary file: {e}")
    else:
        info(f"Successfully created {z.zoneroot}/etc/qemu.cfg")

# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
