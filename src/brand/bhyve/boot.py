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

import bundle
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
import getopt
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
from pprint import pprint, pformat

import uefi.vars as uefivars

# fmt: off

STATEDIR        = '/var/run/bhyve'
RSRVRCTL        = '/usr/lib/rsrvrctl'
FIRMWAREPATH    = '/usr/share/bhyve/firmware'
DEFAULTVARS     = f'{FIRMWAREPATH}/BHYVE_VARS.fd'
MiB             = 1024 * 1024
BOOTROM_SIZE    = 16 * MiB
FBUF_SIZE       = 16 * MiB

# Default values
opts = {
    'acpi':             'on',       # No effect on illumos bhyve
    'bootorder':        'path0,bootdisk,cdrom0',
    "bootnext":         None,
    'bootrom':          'BHYVE_RELEASE_CSM',
    'cloud-init':       'off',
    'console':          '/dev/zconsole',
    'debug.persist':    'off',
    'diskif':           'virtio-blk',
    'hostbridge':       'i440fx',
    'memreserve':       'off',
    'netif':            'virtio-net-viona',
    'priv.debug':       'off',
    'ram':              '1G',
    'rng':              'off',
    'type':             'generic',
    'uefivars':         'on',
    'uuid':             None,
    'vcpus':            '1',
    'vga':              'off',
    'vnc':              'off',
    'xhci':             'on',
}

aliases = {
    'bootorder': {
        'cd':           'path0,bootdisk,cdrom0',
        'dc':           'cdrom0,path0,bootdisk',
    },
    'diskif': {
        'virtio':       'virtio-blk',
        'ahci':         'ahci-hd',
    },
    'netif': {
        'virtio':       'virtio-net-viona',
    },
    'hostbridge': {
        # Was wrongly used in some older scripts.
        'intel':        'netapp',
    },
    'vnc': {
        'on':   'unix=/tmp/vm.vnc',
        'wait': 'unix=/tmp/vm.vnc,wait',
    },
    'bootrom': {
        # These old firmware files were present before r151035. Provide aliases
        # for backwards compatibility.
        'BHYVE_DEBUG-2.70':         'BHYVE_DEBUG',
        'BHYVE_DEBUG_CSM-2.40':     'BHYVE_DEBUG_CSM',
        'BHYVE_RELEASE-2.70':       'BHYVE_RELEASE',
        'BHYVE_RELEASE_CSM-2.40':   'BHYVE_RELEASE_CSM',
    }
}

bootoptions = {
    'shell':    ('app', '7c04a583-9e3e-4f1c-ad65-e05268d0b4d1'),
}

HOSTBRIDGE_SLOT = 0
LPC_SLOT        = 1
CDROM_SLOT      = 3
BOOTDISK_SLOT   = 4
DISK_SLOT       = 5
NET_SLOT        = 6
CDROM_SLOT2     = 7
DISK_SLOT2      = 8
PPT_SLOT        = 9
RNG_SLOT        = 10
VIRTFS_SLOT     = 11
CINIT_SLOT      = 29
VNC_SLOT        = 30
LPC_SLOT_WIN    = 31

# fmt: off

##############################################################################

sysboot         = False
testmode        = False
jsonmode        = False
name            = None
xmlfile         = None

uc = ucred.get(os.getpid())
if not uc.has_priv("Effective", "sys_config"):
    testmode = True

if not testmode:
    try:
        os.mkdir(STATEDIR, mode=0o755)
    except FileExistsError:
        pass

def usage(msg=None):
    print('''
boot [-S] [-t] [-x xml] <[-z] zone>
   -S   System initialisation (host boot) mode
   -t   Test mode - just show what would be done
   -j   Output the computed zone data in JSON format
   -x   Path to zone's XML file
   -z   Name of zone
''')

    if msg: print(msg)
    sys.exit(2)

try:
    cliopts, args = getopt.getopt(sys.argv[1:], "Sjtx:z:")
except getopt.GetoptError:
    usage()
for opt, arg in cliopts:
    if opt == '-S':
        sysboot = True
    elif opt == '-t':
        testmode = True
    elif opt == '-j':
        jsonmode = True
        testmode = True
        bootlib.set_quiet(True)
    elif opt == '-x':
        xmlfile = arg
    elif opt == '-z':
        name = arg
    else:
        fatal(f'unhandled option, {opt}')

if not name and len(args):
    name = args.pop(0)

if len(args):
    usage('Unexpected arguments')

if not name:
    usage('No zone name supplied')

bootlib.log_stdout(logging.DEBUG)

if not xmlfile:
    xmlfile = f'/etc/zones/{name}.xml'

z = bootlib.Zone(xmlfile)

if z.name != name:
    fatal(f'Zone name {name} does not match XML file {z.name}')

if not testmode and not os.path.isdir(z.zoneroot):
    fatal(f'Could not find zone root {z.zoneroot}')

if not testmode:
    try:
        os.unlink(f'{z.zoneroot}/tmp/init.log')
    except:
        pass
    bootlib.log_file(f'{z.zonepath}/log/zone.log', logging.DEBUG)

info(f'Zone name: {name}')

##############################################################################

uefivars_path = None
def install_uefi_vars():
    src = DEFAULTVARS
    dst = '/etc/uefivars'

    global uefivars_path
    uefivars_path = f'{z.zoneroot}{dst}'

    if testmode or os.path.exists(uefivars_path):
        return dst

    if not os.path.exists(src):
        fatal(f'Could not find template UEFI variables file at {src}')

    info('Copying UEFI template variables file')
    shutil.copyfile(src, uefivars_path)
    return dst

def add_bootoption(opt, i, val):
    bootoptions['{}{}'.format(opt, "" if i is None else i)] = val
    if i is not None and i == 0:
        bootoptions[opt] = val

def resolve_bootopt(opt):
    param = None

    m = re.search(rf'^path(\d+)?$', opt)
    if m:
        pathindex = int(m.group(1)) if m.group(1) else 0
        return ('path', pathindex)

    m = re.search(rf'^boot(\d+)$', opt)
    if m:
        return ('boot', int(m.group(1)))

    if opt.startswith('net'):
        if '=' in opt:
            (opt, param) = opt.split('=')
        else:
            param = None
        if param and param not in ['pxe', 'http']:
            fatal(f"Invalid protocol '{param}' for '{opt}'")
        if param == 'pxe':
            param = None

    try:
        opt = bootoptions[opt]
    except KeyError:
        return None

    if param:
        return opt + (param,)

    return opt

def apply_bootorder(v):
    if not opts['bootorder']:
        return
    bootorder = []
    for opt in opts['bootorder'].split(','):
        t = resolve_bootopt(opt)
        if t:
            bootorder.append(t)

    debug(f'For requested bootorder {opts["bootorder"]}')
    debug(f'... setting to: {pformat(bootorder)}')

    try:
        v.set_bootorder(bootorder)
    except Exception as e:
        error(f'Could not set VM boot order: {e}')

def apply_bootnext(v):
    opt = opts['bootnext']
    if not opt:
        return
    nxt = resolve_bootopt(opt)
    if not nxt:
        return

    debug(f'Setting bootnext to: {nxt}')

    try:
        v.set_bootnext(nxt)
    except Exception as e:
        error(f'Could not set VM boot next: {e}')

    subprocess.run(['/usr/sbin/zonecfg', '-z', zone,
        'remove attr name=bootnext'])

##############################################################################

for tag in opts.keys():
    z.parseopt(tag, opts, aliases)

if boolv(opts['memreserve'], 'memreserve'):
    # In memreserve mode, the memory for this VM needs to be reserved in
    # the bhyve memory reservoir, then the VM must be told to use memory
    # from that reservoir (see later).
    mem = bootlib.parse_ram(opts['ram'])
    mem += BOOTROM_SIZE
    if  boolv(opts['vnc'], 'vnc', ignore=True) is not False:
        mem += FBUF_SIZE

    try:
        with open(f'{STATEDIR}/{name}.resv') as f:
            oldmem = int(f.read().strip())
    except OSError:
        oldmem = 0

    # Update the reservoir if necessary
    if not testmode and oldmem != mem:
        delta = mem - oldmem
        if delta > 0:
            delta //= MiB
            op = '-a'
        else:
            delta //= -MiB
            op = '-r'
        debug(f'RAM change from {oldmem} to {mem} - {op} {delta}')
        ret = subprocess.run([RSRVRCTL, op, str(delta)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for l in ret.stdout.splitlines():
            debug(l)

        try:
            fh = tempfile.NamedTemporaryFile(mode='w', dir=f'{STATEDIR}',
                prefix=f'{name}.resv.', delete=False)
        except Exception as e:
            fatal(f'Could not create temporary file: {e}')
        else:
            debug(f'Created temporary file at {fh.name}')

        tf = fh.name
        fh.write(str(mem))
        fh.close()
        try:
            os.rename(tf, f'{STATEDIR}/{name}.resv')
        except Exception as e:
            fatal(f'Could not create {name}.resv from temporary file: {e}')
        else:
            debug(f'Successfully created {STATEDIR}/{name}.resv')

        info(f'{mem//MiB} MiB of RAM reserved in memory reservoir')

# This may be being called during system boot for a VM which does not
# have autoboot set on it, and in that case there is nothing more to do.
if sysboot:
    sys.exit(0)

if opts['type'] == 'windows':
    # See https://wiki.freebsd.org/bhyve/Windows
    # ... There are currently some slot limitations with UEFI:
    # ... - AHCI devices must be in slots 3/4/5/6
    # ... - The PCI-ISA bus aka lpc must be in slot 31
    info('Adjusting LPC PCI slot for windows')
    LPC_SLOT = LPC_SLOT_WIN

# Bootrom
bootrom = opts['bootrom']
if not os.path.isabs(bootrom):
    bootrom = f'{FIRMWAREPATH}/{bootrom}'
    if not bootrom.endswith('.fd'):
        bootrom += '.fd'
    if not os.path.isfile(bootrom):
        fatal(f'bootrom {opts["bootrom"]} not found.')
debug(f'Final bootrom: {bootrom}')

uefi_bootrom = boolv(opts['uefivars'], 'uefivars') and '_CSM' not in bootrom

# If we don't have a UEFI bootrom, only basic boot order selection is
# possible, and  we attempt to honour the request to favour the hard disk
# by moving the CDROM device to a higher PCI slot.
if not uefi_bootrom and opts['bootorder'].startswith('bootdisk') and \
    opts['type'] != 'windows':
    CDROM_SLOT = CDROM_SLOT2

# UUID
uuid = opts['uuid'] if opts['uuid'] else z.uuid()
debug(f'Final uuid: {uuid}')

##############################################################################

args = ['/usr/sbin/bhyve']

ser = uuid

if boolv(opts['cloud-init'], 'cloud-init', ignore=True) is not False:
    if opts['cloud-init'].startswith(('http://', 'https://')):
        ser = 'ds=nocloud-net;s={};i={}'.format(opts['cloud-init'], uuid)
    else:
        z.build_cloudinit_image(uuid, opts['cloud-init'], testmode)
        ser = f'ds=nocloud;i={uuid}'
        args.extend([ '-s',
            '{0}:0,ahci-cd,/cloud-init.iso,ro,ser=CLOUD-INIT-0'
            .format(CINIT_SLOT)
        ])

if opts['type'] == 'openbsd':
    info('Ignoring unknown MSRs for OpenBSD')
    args.append('-w')

if uuid:
    args.extend(['-U', uuid])

args.extend([
    '-H',
    '-B', '1,manufacturer={},product={},version={},serial={},sku={},family={}'
        .format('OmniOS', 'OmniOS HVM', '1.0', ser, '001', 'Virtual Machine'),
    '-c', opts['vcpus'],
    '-m', opts['ram'],
])

# Bootrom

if uefi_bootrom:
    bootvars = install_uefi_vars()
    args.extend(['-l', f'bootrom,{bootrom},{bootvars}'])
else:
    args.extend(['-l', f'bootrom,{bootrom}'])

# Host bridge

if not opts['hostbridge'] or opts['hostbridge'] == 'none':
    pass
elif '=' in opts['hostbridge']:
    args.extend(['-s', '{0},hostbridge,{1}'.format(
        HOSTBRIDGE_SLOT, opts['hostbridge'])])
else:
    args.extend(['-s', '{0},hostbridge,model={1}'.format(
        HOSTBRIDGE_SLOT, opts['hostbridge'])])

# LPC

args.extend(['-s', '{0},lpc'.format(LPC_SLOT)])

# Console

args.extend(['-l', 'com1,{0}'.format(opts['console'])])

# CDROM

for i, v in z.build_devlist('cdrom', 8):
    args.extend([
        '-s', '{0}:{1},{2},{3},ro'.format(CDROM_SLOT, i, 'ahci-cd', v)
    ])
    add_bootoption('cdrom', i, ('pci', f'{CDROM_SLOT}.{i}'))

# Bootdisk

try:
    bootdisk = z.findattr('bootdisk')
    args.extend([
        '-s', '{0}:0,{1},{2}'.format(BOOTDISK_SLOT, opts['diskif'],
            diskpath(bootdisk.get('value').strip()))
    ])
    add_bootoption('bootdisk', None, ('pci', f'{BOOTDISK_SLOT}.0'))
except:
    pass

# Additional Disks

for i, v in z.build_devlist('disk', 16):
    if (vv := z.findattr(f'diskif{i}')) is not None:
        diskif = vv.get('value')
        try:
            diskif = aliases['diskif'][diskif]
        except KeyError:
            pass
    else:
        diskif = opts['diskif']

    if i < 8:
        args.extend([
            '-s', '{0}:{1},{2},{3}'.format(DISK_SLOT, i, diskif,
            diskpath(v))
        ])
        add_bootoption('disk', i, ('pci', f'{DISK_SLOT}.{i}'))
    else:
        args.extend([
            '-s', '{0}:{1},{2},{3}'.format(DISK_SLOT2, i - 8, diskif,
            diskpath(v))
        ])
        add_bootoption(f'disk', i, ('pci', f'{DISK_SLOT2}.{i - 8}'))

# Network

i = 0
promisc_filtered_nics = {}
for f in z.findall('./network[@physical]'):
    ifname = f.get('physical').strip()

    netif = opts['netif']
    net_extra = ''
    for a in f.findall('./net-attr[@name]'):
        k, v = a.get('name'), a.get('value')
        if k == "netif":
            netif = v
        else:
            if k == "promiscphys":
                promisc_filtered_nics[ifname] = 'off' if boolv(v, k) else 'on'
            elif k in ['promiscrxonly', 'promiscsap', 'promiscmulti']:
                boolv(v, k)  # Value check
            net_extra += ',{}={}'.format(k, v)

    args.extend([
        '-s', '{0}:{1},{2},{3}{4}'
        .format(NET_SLOT, i, netif, ifname, net_extra)
    ])
    add_bootoption('net', i, ('pci', f'{NET_SLOT}.{i}'))
    i += 1

for nic, promisc in promisc_filtered_nics.items():
    dladm_args = [
        '/usr/sbin/dladm', 'set-linkprop',
        '-p', f'promisc-filtered={promisc}',
        nic,
    ]

    debug(f'Setting promisc-filtered for {nic} to {promisc}')
    p = subprocess.run(dladm_args, capture_output=True, text=True)
    if p.returncode > 0:
        fatal(f'Could set promisc-filtered for {nic}: {p.stderr}')

# VNC

vncpassword = None
v = boolv(opts['vnc'], 'vnc', ignore=True)
if v is not False:
    if v is True:
        opts['vnc'] = 'on'

    # The VNC options need to be processed in order to extract and mask
    # the 'password' attribute and to handle aliases for other elements.
    vopts = expandopts(opts['vnc'])
    for k, v in list(vopts.items()):
        if not len(v):
            try:
                newopts = expandopts(aliases['vnc'][k])
            except KeyError:
                pass
            else:
                del vopts[k]
                # This way round means that the keys from vopts will overwrite
                # any duplicates in newopts.
                newopts |= vopts
                vopts = newopts
        elif k == 'password':
            vncpassword = bootlib.file_or_string(v)
            del vopts[k]

    opts['vnc'] = collapseopts(vopts)

    args.extend(['-s', '{0}:0,fbuf,vga={1},{2}'.format(
        VNC_SLOT, opts['vga'], opts['vnc'])])
    if boolv(opts['xhci'], 'xhci'):
        args.extend(['-s', '{0}:1,xhci,tablet'.format(VNC_SLOT)])

# PPT - PCI Pass-through devices

pptlist = z.build_devlist('ppt', 8, False)
pptassign = {}

# Build the PPT list in two passes looking for devices with a specifically
# assigned slot first, and then fitting any others into the gaps.
for i, v in pptlist:
    m = re.search(rf'^slot(\d+)$', v)
    if not m: continue
    slot = int(m.group(1))
    if slot in pptassign:
        fatal(f'ppt slot {slot} appears more than once')
    if slot < 0 or slot > 7:
        fatal(f'ppt slot {slot} out of range (0-7)')
    pptassign[slot] = f'ppt{i}'

pptavail = sorted(set(range(0, 7)).difference(sorted(pptassign.keys())))
for i, v in pptlist:
    if not boolv(v, f'ppt{i}', ignore=True): continue
    try:
        slot = pptavail.pop(0)
    except IndexError:
        fatal('ppt: no more available slots')
    pptassign[slot] = f'ppt{i}'

if len(pptassign) > 0:
    args.append('-S')
    for i, v in sorted(pptassign.items()):
        args.extend(['-s', '{0}:{1},passthru,/dev/{2}'.format(
            PPT_SLOT, i, v)])

# virtio-9p devices

for i, v in z.build_devlist('virtfs', 8):
    # Expect <sharename>,<path>[,ro]
    arr = v.split(',')
    vfsopts = ''
    if len(arr) == 3:
        if arr[2] != 'ro':
            fatal(f'Unknown virtfs attribute {arr[2]}')
        vfsopts = ',ro'
    elif len(arr) != 2:
        fatal(f'Bad virtfs specification: {v}')
    args.extend(['-s', '{0}:{1},virtio-9p,{2}={3}{4}'.format(
        VIRTFS_SLOT, i, arr[0], arr[1], vfsopts)])

# RNG

if boolv(opts['rng'], 'rng'):
    args.extend(['-s', '{0}:0,virtio-rnd'.format(RNG_SLOT)])

# priv.debug
if boolv(opts['priv.debug'], 'priv.debug'):
    args.extend(['-o', 'privileges.debug=true'])

# debug.persist
if boolv(opts['debug.persist'], 'debug.persist'):
    args.extend(['-o', 'debug.persist=true'])

# memreserve
if boolv(opts['memreserve'], 'memreserve'):
    args.extend(['-o', 'memory.use_reservoir=true'])

# Extra options

for i, v in z.build_devlist('extra', 16):
    args.extend(shlex.split(v))

# Dump configuration

args.extend(['-o', 'config.dump=1'])

# VM name

args.append(name)

##############################################################################

debug(f'Final bootoptions:\n{pformat(bootoptions)}')
if uefivars_path and not testmode:
    v = uefivars.UEFIVars(uefivars_path)
    for i in sorted(v.bootmap.keys()):
        debug(f'Boot{i:04x} - {v.bootmap[i]}')
    debug('-----------')
    for k, i in v.bootrmap.items():
        debug(f'{k} -> Boot{i:04x}')
    apply_bootorder(v)
    apply_bootnext(v)
    try:
        v.write()
    except Exception as e:
        info(f'Could not write boot options: {e}')

debug(f'Final arguments:\n{pformat(args)}')
info('{0}'.format(' '.join(map(
    lambda s: f'"{s}"' if ' ' in s else s, args))))

p = subprocess.run(args, capture_output=True, text=True)
# config.dump exits with a status code of 1. Other errors indicate a problem.
if p.returncode != 1:
    fatal(f'Could not parse configuration: {p.stderr}')

if jsonmode:
    from itertools import zip_longest
    import json

    # fmt: off

    data = {
        'zonename':     z.name,
        'zonepath':     z.zonepath,
        'zoneroot':     z.zoneroot,
        'slots': {
            "hostbridge":   HOSTBRIDGE_SLOT,
            "lpc":          LPC_SLOT,
            "cdrom":        CDROM_SLOT,
            "bootdisk":     BOOTDISK_SLOT,
            "disk":         DISK_SLOT,
            "net":          NET_SLOT,
            "ppt":          PPT_SLOT,
            "rng":          RNG_SLOT,
            "virtfs":       VIRTFS_SLOT,
            "cloudinit":    CINIT_SLOT,
            "vnc":          VNC_SLOT,
        },
        'uefi':         uefi_bootrom,
        'bootoptions':  bootoptions,
        'opts':         opts,
        'config':       {},
    }

    # fmt: on

    for line in p.stdout.splitlines():
        if line.startswith('config.dump'): continue
        if '=' not in line: continue
        (k, v) = line.split('=', maxsplit=1)

        keys = k.split('.')
        p = data['config']
        for (a, b) in zip_longest(keys, keys[1:]):
            if b is None:   # tail
                p[a] = v
                break
            if a not in p:
                p[a] = {}
            p = p[a]

    print(json.dumps(data))
    sys.exit(0)

def writecfg(fh, arg):
    if testmode:
        print(arg)
    else:
        fh.write(f'{arg}\n')

fh = None
if not testmode:
    try:
        fh = tempfile.NamedTemporaryFile(mode='w', dir=f'{z.zoneroot}/etc',
            prefix='bhyve.', delete=False)
    except Exception as e:
        fatal(f'Could not create temporary file: {e}')
    else:
        debug(f'Created temporary file at {fh.name}')

writecfg(fh, '#\n# Generated from zone configuration\n#')

for line in p.stdout.splitlines():
    if line.startswith('config.dump'): continue
    writecfg(fh, line)

if vncpassword:
    writecfg(fh, f'pci.0.{VNC_SLOT}.0.password={vncpassword}')

if not testmode:
    tf = fh.name
    fh.close()
    try:
        os.rename(tf, f'{z.zoneroot}/etc/bhyve.cfg')
    except Exception as e:
        fatal(f'Could not create bhyve.cfg from temporary file: {e}')
    else:
        info(f'Successfully created {z.zoneroot}/etc/bhyve.cfg')

# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
