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

# Copyright 2021 OmniOS Community Edition (OmniOSce) Association.

import logging, os, subprocess, sys, time, re, getopt, shlex
import xml.etree.ElementTree as etree
from pprint import pprint, pformat

zonexml = "/etc/zone.xml"
uuidfile = "/etc/uuid"
testmode = False

try:
    opts, args = getopt.getopt(sys.argv[1:], "tx:u:")
except getopt.GetoptError:
    print("init [-t] [-x <xml file>] [-u <uuid file>]")
    sys.exit(2)
for opt, arg in opts:
    if opt == "-t":
        testmode = True
    elif opt == "-x":
        zonexml = arg
    elif opt == "-u":
        uuidfile = arg

if testmode:
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
else:
    logging.basicConfig(
        filename="/tmp/init.log", filemode="a", level=logging.DEBUG
    )

# fmt: off

# Default values
opts = {
    'vcpus':        '1',
    'ram':          '1G',
    'diskif':       'virtio',
    'netif':        'virtio-net-pci',
    'type':         'generic',
    'vnc':          'none',
    'console':      'pipe,id=console0,path=/dev/zconsole',
    'cpu':          'qemu64',
    'bootorder':    'cd',
    'rtc':          'base=utc,driftfix=slew',
}

aliases = {
    'diskif': {
        'virtio':       'virtio',
        'ahci':         'ahci',
    },
    'netif': {
        'virtio':       'virtio-net-pci',
    },
    'vnc': {
        'on':           'unix:/tmp/vm.vnc',
        'off':          'none',
    }
}

# fmt: on

try:
    with open(uuidfile) as file:
        uuid = file.read().strip()
        logging.info("Zone UUID: {0}".format(uuid))
except:
    uuid = None

try:
    cfg = etree.parse(zonexml)
except:
    logging.error("Could not parse {0}".format(zonexml))
    sys.exit(1)
root = cfg.getroot()
logging.info("Parsed {0}".format(zonexml))

##############################################################################


def fatal(msg):
    logging.error(msg)
    print(msg, file=sys.stderr)
    sys.exit(1)


def boolv(s, var, ignore=False):
    if s in ["true", "yes", "on", "1"]:
        return True
    if s in ["false", "no", "off", "0"]:
        return False
    if not ignore:
        fatal(f"Invalid value {s} for boolean variable {var}")
    return None


def opt(tag):
    global opts, root
    try:
        el = root.find('./attr[@name="{0}"]'.format(tag))
        opts[tag] = el.get("value").strip()
        logging.debug(
            'Found custom {0} attribute - "{1}"'.format(tag, opts[tag])
        )
        if tag in aliases:
            val = opts[tag]
            if (bt := boolv(val, tag, ignore=True)) is not None:
                val = "on" if bt else "off"
            try:
                opts[tag] = aliases[tag][val]
                logging.debug("  Alias expanded to {0}".format(opts[tag]))
            except KeyError:
                pass
    except:
        pass


def diskpath(arg):
    if arg.startswith("/dev"):
        return arg
    return "/dev/zvol/rdsk/{0}".format(arg)


for tag in opts.keys():
    opt(tag)


# Look for attributes of the form <type> or <type>N and generate a list.
def build_devlist(type, maxval):
    devlist = {}
    for dev in root.findall("./attr[@name]"):
        m = re.search(rf"^{type}(\d+)$", dev.get("name").strip())
        if not m:
            continue
        k = int(m.group(1))
        if k in devlist:
            logging.error(
                "{}{} appears more than once in configuration".format(type, k)
            )
            sys.exit(1)
        if k > maxval:
            logging.error("{}{} slot out of range".format(type, k))
            sys.exit(1)
        devlist[k] = dev.get("value").strip()

    # Now insert plain <type> tags into the list, using available slots in order
    avail = sorted(set(range(0, maxval)).difference(sorted(devlist.keys())))
    for dev in root.findall(f'./attr[@name="{type}"]'):
        try:
            k = avail.pop(0)
        except IndexError:
            logging.error("{}: no more available slots".format(type))
            sys.exit(1)
        devlist[k] = dev.get("value").strip()

    logging.debug("{} list: \n{}".format(type, pformat(devlist)))

    return devlist


##############################################################################

args = ["/usr/bin/qemu-system-x86_64"]

name = root.get("name")
args.extend(["-name", name])

if uuid:
    args.extend(["-uuid", uuid])

args.extend(
    [
        "-enable-kvm",
        "-no-hpet",
        "-m",
        opts["ram"],
        "-smp",
        opts["vcpus"],
        "-cpu",
        opts["cpu"],
        "-rtc",
        opts["rtc"],
        "-pidfile",
        "/tmp/vm.pid",
        "-monitor",
        "unix:/tmp/vm.monitor,server,nowait,nodelay",
        "-vga",
        "std",
        "-chardev",
        opts["console"],
        "-serial",
        "chardev:console0",
        "-boot",
        "order={0}".format(opts["bootorder"]),
    ]
)

# Disks


def add_disk(path, boot=False, intf=None, media="disk", index=-1):
    global args

    if not intf:
        intf = opts["diskif"]

    if index < 0:
        index = add_disk.index
        add_disk.index += 1
    elif index > add_disk.index:
        add_disk.index = index

    if media == "disk":
        path = diskpath(path)
    str = "file={0},if={1},media={2},index={3},cache=none".format(
        path, intf, media, index
    )
    if ",serial=" not in str:
        str += ",serial={}{}".format(media, index)
    if boot:
        str += ",boot=on"
    args.extend(["-drive", str])


add_disk.index = 0

for i, v in build_devlist("cdrom", 5).items():
    add_disk(v, boot=False, intf="ide", media="cdrom")

# If the disks are not using IDE, then reset their index as there is no need
# to leave room for the CDROM.
if opts["diskif"] != "ide":
    add_disk.index = 0

try:
    bootdisk = root.find('./attr[@name="bootdisk"]')
    add_disk(bootdisk.get("value").strip(), boot=True)
except:
    pass

for i, v in build_devlist("disk", 10).items():
    add_disk(v)

# Network


def get_mac(ifname):
    ret = subprocess.run(
        ["/usr/sbin/dladm", "show-vnic", "-p", "-o", "macaddress", ifname],
        stdout=subprocess.PIPE,
    )
    mac = ret.stdout.decode("utf-8").strip()
    # Need to zero-pad the bytes
    return ":".join(l.zfill(2) for l in mac.split(":"))


vlan = 0
for f in root.findall("./network[@physical]"):
    ifname = f.get("physical").strip()
    mac = get_mac(ifname)
    if not len(mac):
        continue

    if opts["netif"] == "e1000":
        # -net nic,vlan=0,name=net0,model=e1000,macaddr=00:..
        args.extend(
            [
                "-net",
                "nic,vlan={2},name=net{2},model={0},macaddr={1}".format(
                    opts["netif"], mac, vlan
                ),
            ]
        )
    else:
        # -device <dev>,mac=00:.,tx=timer,x-txtimer=200000,x-txburst=128,vlan=0
        args.extend(
            [
                "-device",
                "{0},mac={1},tx=timer,x-txtimer=200000,x-txburst=128,vlan={2}".format(
                    opts["netif"], mac, vlan
                ),
            ]
        )

    # -net vnic,vlan=0,name=net0,ifname=vnic22
    args.extend(
        ["-net", "vnic,vlan={0},name=net{0},ifname={1}".format(vlan, ifname)]
    )

    vlan += 1

# VNC

args.extend(["-vnc", opts["vnc"]])

# Extra options

for i, v in build_devlist("extra", 16):
    args.extend(shlex.split(v))

logging.info("Final arguments: {0}".format(pformat(args)))
logging.info("{0}".format(" ".join(args)))

if testmode:
    sys.exit(0)

errcnt = 0
while errcnt < 10:
    logging.info("Starting kvm")
    ret = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logging.info("KVM exited {0}".format(ret.returncode))
    logging.error("Error {0}".format(ret.stderr))
    logging.debug("Output {0}".format(ret.stdout))
    if ret.returncode == 0:
        break
    if ret.returncode == 1:
        errcnt += 1

# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
