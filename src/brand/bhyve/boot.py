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

# Copyright 2022 OmniOS Community Edition (OmniOSce) Association.

import bundle
import getopt
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import ucred
import yaml
import xml.etree.ElementTree as etree
from pprint import pprint, pformat

import uefi.vars as uefivars

STATEDIR        = '/var/run/bhyve'
RSRVRCTL        = '/usr/lib/rsrvrctl'
FIRMWAREPATH    = '/usr/share/bhyve/firmware'
DEFAULTVARS     = f'{FIRMWAREPATH}/BHYVE_VARS.fd'
MiB             = 1024 * 1024
BOOTROM_SIZE    = 16 * MiB
FBUF_SIZE       = 16 * MiB

# Default values
opts = {
    'acpi':             'on',
    'bootorder':        'path0,bootdisk,cdrom0',
    "bootnext":         None,
    'bootrom':          'BHYVE_RELEASE_CSM',
    'cloud-init':       'off',
    'console':          '/dev/zconsole',
    'debug.persist':    'off',
    'diskif':           'virtio-blk',
    'extra':            None,
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

##############################################################################

sysboot         = False
testmode        = False
zone            = None
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
   -x   Path to zone's XML file
   -z   Name of zone
''')

    if msg: print(msg)
    sys.exit(2)

try:
    cliopts, args = getopt.getopt(sys.argv[1:], "Stx:z:")
except getopt.GetoptError:
    usage()
for opt, arg in cliopts:
    if opt == '-S':
        sysboot = True
    elif opt == '-t':
        testmode = True
    elif opt == '-x':
        xmlfile = arg
    elif opt == '-z':
        zone = arg
    else:
        assert False, f'unhandled option, {opt}'

if not zone and len(args):
    zone = args.pop(0)

if len(args):
    usage('Unexpected arguments')

if not zone:
    usage('No zone name supplied')

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

def fatal(msg):
    logging.error(msg)
    print(msg, file=sys.stderr)
    sys.exit(1)

if not xmlfile:
    xmlfile = f'/etc/zones/{zone}.xml'

if not os.path.isfile(xmlfile):
    fatal(f'Cannot find zone XML file at {xmlfile}')

try:
    cfg = etree.parse(xmlfile)
except:
    fatal(f'Could not parse {xmlfile}')

xmlroot = cfg.getroot()

name = xmlroot.get('name')
zonepath = xmlroot.get('zonepath')
zoneroot = f'{zonepath}/root'

if not testmode and not os.path.isdir(zoneroot):
    fatal(f'Could not find zone root {zoneroot}')

if not testmode:
    try:
        os.unlink(f'{zoneroot}/tmp/init.log')
    except:
        pass
    logging.basicConfig(filename=f'{zonepath}/log/zone.log', filemode='a',
        level=logging.DEBUG, force=True)

logging.info(f'Zone name: {name}')

##############################################################################

def boolv(s, var, ignore=False):
    if s in ['true', 'yes', 'on', '1']:
        return True
    if s in ['false', 'no', 'off', '0']:
        return False
    if not ignore:
        fatal(f'Invalid value {s} for boolean variable {var}')
    return None

def file_or_string(f):
    if os.path.isabs(f) and os.path.isfile(f):
        try:
            with open(f) as fh:
                f = fh.read()
        except Exception as e:
            fatal(f'Could not read from {f}: {e}')
    return f.strip()

def parseopt(tag):
    global opts, xmlroot
    try:
        el = xmlroot.find('./attr[@name="{0}"]'.format(tag))
        opts[tag] = el.get('value').strip()
        logging.debug('Found custom {0} attribute - "{1}"'
            .format(tag, opts[tag]))
        if tag in aliases:
            val = opts[tag]
            if (bt := boolv(val, tag, ignore=True)) is not None:
                val = 'on' if bt else 'off'
            try:
                opts[tag] = aliases[tag][val]
                logging.debug('  Alias expanded to {0}'.format(opts[tag]))
            except KeyError:
                pass
    except:
        pass

def expandopts(opt):
    """ Expand a comma-separated option string out into a dictionary.
        For example:
            on,password=fred,wait,w=1234
        becomes:
            {'on': '', 'password': 'fred', 'wait': '', 'w': '1234'}
    """
    return {
        k: v
        for (k, v, *_)
        in [
            (o + '=').split('=')
            for o in opt.split(',')
        ]
    }

def collapseopts(opts):
    """ The reverse of expandopts. Convert a dictionary back into an option
        string. """
    return ','.join([f'{k}={v}'.rstrip('=') for k, v in opts.items()])

def writecfg(fh, arg):
    if testmode:
        print(arg)
    else:
        fh.write(f'{arg}\n')

def diskpath(arg):
    if arg.startswith('/'):
        return arg
    return '/dev/zvol/rdsk/{0}'.format(arg)

def findattr(rex):
    for dev in xmlroot.findall('./attr[@name]'):
        if m := re.search(rex, dev.get('name').strip()):
            yield dev, m

# Look for attributes of the form <type>N (and <type> if plain is True) and
# generate a list.
def build_devlist(type, maxval, plain=True):
    devlist = {}
    for dev, m in findattr(rf'^{type}(\d+)$'):
        k = int(m.group(1))
        if k in devlist:
            fatal(f'{type}{k} appears more than once in configuration')
        if (k >= maxval):
            fatal(f'{type}{k} slot out of range')
        devlist[k] = dev.get('value').strip()

    if plain:
        # Now insert plain <type> tags into the list, using available slots in
        # order
        avail = sorted(set(range(0, maxval)).difference(sorted(devlist.keys())))
        for dev in xmlroot.findall(f'./attr[@name="{type}"]'):
            try:
                k = avail.pop(0)
            except IndexError:
                fatal('{type}: no more available slots')
            devlist[k] = dev.get('value').strip()

    logging.debug('{} list: \n{}'.format(type, pformat(devlist)))

    return sorted(devlist.items())

ram_shift = { 'e': 60, 'p': 50, 't': 40, 'g': 30, 'm': 20, 'k': 10, 'b': 0 }
def parse_ram(v):
    # Parse a string representing an amount of RAM into bytes in the same
    # way as libvmmapi's vm_parse_memsize()
    m = re.search(rf'^(\d+)(.?)$', v)
    if not m:
        fatal(f'Could not parse ram value "{v}"')
    (mem, suffix) = m.groups()
    mem = int(mem)

    if not suffix:
        # If the memory size specified as a plain number is less than a
        # mebibyte then bhyve will interpret it as being in units of MiB.
        suffix = 'm' if mem < MiB else 'b'

    try:
        shift = ram_shift[suffix.lower()]
    except KeyError:
        fatal(f'Unknown RAM suffix, {suffix}')

    mem <<= shift

    logging.debug(f'parse_ram({v}) = {mem}')
    return mem

def build_cloudinit_image(uuid, src):
    logging.info('Generating cloud-init data')

    #### Metadata

    meta_data = {
        'instance-id':      uuid,
        'local-hostname':   name,
    }

    #### Userdata

    user_data = {
        'hostname':         name,
        'disable_root':     False,
    }

    if (v := xmlroot.find('./attr[@name="password"]')) is not None:
        user_data['password'] = file_or_string(v.get('value'))
        user_data['chpasswd'] = { 'expire': False }
        user_data['ssh-pwauth'] = True

    if (v := xmlroot.find('./attr[@name="sshkey"]')) is not None:
        v = file_or_string(v.get('value'))
        user_data['ssh_authorized_keys'] = [v]
        user_data['users'] = [
            'default',
            {'name': 'root', 'ssh_authorized_keys': [v]}
        ]

    #### Network

    network_data = {}

    addresses = xmlroot.findall('./network[@allowed-address]')
    if addresses is not None:
        nsdone = False
        network_data['version'] = 2
        network_data['ethernets'] = {}

        for a in addresses:
            vnic = a.get('physical')
            addr = a.get('allowed-address')
            rtr = a.get('defrouter')

            p = subprocess.run([ '/usr/sbin/dladm',
                'show-vnic', '-p', '-o', 'MACADDRESS', vnic ],
                capture_output=True, text=True)
            if p.returncode != 0:
                logging.warning(f'Could not find MAC address for VNIC {vnic}')
                continue
            mac = p.stdout.strip()
            mac = ':'.join(l.zfill(2) for l in mac.split(':'))

            data = {
                'match':        { 'macaddress': mac },
                'set-name':     vnic,
                'addresses':    [addr],
            }
            if rtr:
                data['gateway4'] = rtr

            if not nsdone:
                domain = xmlroot.find('./attr[@name="dns-domain"]')
                resolvers = xmlroot.find('./attr[@name="resolvers"]')
                if resolvers is not None or domain is not None:
                    nsdata = {}
                    if domain is not None:
                        nsdata['search'] = [domain.get('value').strip()]
                    if resolvers is not None:
                        nsdata['addresses'] = \
                            resolvers.get('value').strip().split(',')
                    data['nameservers'] = nsdata
                nsdone = True

            network_data['ethernets'][vnic] = data

    logging.debug('Metadata:\n' + yaml.dump(meta_data))
    logging.debug('Userdata:\n' + yaml.dump(user_data))
    logging.debug('Netdata:\n' + yaml.dump(network_data))

    if testmode:
        return

    dir = tempfile.mkdtemp(dir=f'{zoneroot}', prefix='cloud-init.')

    with open(f'{dir}/meta-data', 'w') as fh:
        yaml.dump(meta_data, fh)

    if os.path.isabs(src) and os.path.isfile(src):
        logging.info(f'Using supplied cloud-init user-data file from {src}')
        shutil.copyfile(src, f'{dir}/user-data')
    else:
        with open(f'{dir}/user-data', 'w') as fh:
            fh.write('#cloud-config\n')
            yaml.dump(user_data, fh)

    if network_data:
        with open(f'{dir}/network-config', 'w') as fh:
            yaml.dump(network_data, fh)

    #### Build image

    cidir = f'{zoneroot}/cloud-init'
    seed = f'{zoneroot}/cloud-init.iso'
    if os.path.exists(cidir):
        shutil.rmtree(cidir)
    os.rename(dir, cidir)
    logging.info('Building cloud-init ISO image')
    try:
        ret = subprocess.run([
            '/usr/bin/mkisofs',
            '-full-iso9660-filenames',
            '-untranslated-filenames',
            '-rock',
            '-volid', 'CIDATA',
            '-o', seed,
            cidir
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for l in ret.stdout.splitlines():
            logging.info(l)
        os.chmod(seed, mode=0o644)
    except Exception as e:
        fatal(f'Could not create cloud-init ISO image: {e}')

uefivars_path = None
def install_uefi_vars():
    src = DEFAULTVARS
    dst = '/etc/uefivars'

    global uefivars_path
    uefivars_path = f'{zoneroot}{dst}'

    if testmode or os.path.exists(uefivars_path):
        return dst

    if not os.path.exists(src):
        fatal(f'Could not find template UEFI variables file at {src}')

    logging.info('Copying UEFI template variables file')
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
        (opt, param) = opt.split('=')
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

    logging.debug(f'For requested bootorder {opts["bootorder"]}')
    logging.debug(f'... setting to: {pformat(bootorder)}')

    try:
        v.set_bootorder(bootorder)
    except Exception as e:
        logging.error(f'Could not set VM boot order: {e}')

def apply_bootnext(v):
    opt = opts['bootnext']
    if not opt:
        return
    nxt = resolve_bootopt(opt)
    if not nxt:
        return

    logging.debug(f'Setting bootnext to: {nxt}')

    try:
        v.set_bootnext(nxt)
    except Exception as e:
        logging.error(f'Could not set VM boot next: {e}')

    subprocess.run(['/usr/sbin/zonecfg', '-z', zone,
        'remove attr name=bootnext'])

##############################################################################

for tag in opts.keys():
    parseopt(tag)

if boolv(opts['memreserve'], 'memreserve'):
    # In memreserve mode, the memory for this VM needs to be reserved in
    # the bhyve memory reservoir, then the VM must be told to use memory
    # from that reservoir (see later).
    mem = parse_ram(opts['ram'])
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
        logging.debug(f'RAM change from {oldmem} to {mem} - {op} {delta}')
        ret = subprocess.run([RSRVRCTL, op, str(delta)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for l in ret.stdout.splitlines():
            logging.debug(l)

        try:
            fh = tempfile.NamedTemporaryFile(mode='w', dir=f'{STATEDIR}',
                prefix=f'{name}.resv.', delete=False)
        except Exception as e:
            fatal(f'Could not create temporary file: {e}')
        else:
            logging.debug(f'Created temporary file at {fh.name}')

        tf = fh.name
        fh.write(str(mem))
        fh.close()
        try:
            os.rename(tf, f'{STATEDIR}/{name}.resv')
        except Exception as e:
            fatal(f'Could not create {name}.resv from temporary file: {e}')
        else:
            logging.debug(f'Successfully created {STATEDIR}/{name}.resv')

        logging.info(f'{mem//MiB} MiB of RAM reserved in memory reservoir')

# This may be being called during system boot for a VM which does not
# have autoboot set on it, and in that case there is nothing more to do.
if sysboot:
    sys.exit(0)

if opts['type'] == 'windows':
    # See https://wiki.freebsd.org/bhyve/Windows
    # ... There are currently some slot limitations with UEFI:
    # ... - AHCI devices must be in slots 3/4/5/6
    # ... - The PCI-ISA bus aka lpc must be in slot 31
    logging.info('Adjusting LPC PCI slot for windows')
    LPC_SLOT = LPC_SLOT_WIN

# Bootrom
bootrom = opts['bootrom']
if not os.path.isabs(bootrom):
    bootrom = f'{FIRMWAREPATH}/{bootrom}'
    if not bootrom.endswith('.fd'):
        bootrom += '.fd'
    if not os.path.isfile(bootrom):
        fatal(f'bootrom {opts["bootrom"]} not found.')
logging.debug(f'Final bootrom: {bootrom}')

uefi_bootrom = boolv(opts['uefivars'], 'uefivars') and '_CSM' not in bootrom

# If we don't have a UEFI bootrom, only basic boot order selection is
# possible, and  we attempt to honour the request to favour the hard disk
# by moving the CDROM device to a higher PCI slot.
if not uefi_bootrom and opts['bootorder'].startswith('bootdisk') and \
    opts['type'] != 'windows':
    CDROM_SLOT = CDROM_SLOT2

# UUID
uuid = opts['uuid']
if not uuid:
    try:
        with open(f'{zoneroot}/etc/uuid') as file:
            uuid = file.read().strip()
            logging.info('Zone UUID: {0}'.format(uuid))
    except:
        uuid = None
logging.debug(f'Final uuid: {uuid}')

##############################################################################

args = ['/usr/sbin/bhyve']

ser = uuid

if boolv(opts['cloud-init'], 'cloud-init', ignore=True) is not False:
    if opts['cloud-init'].startswith(('http://', 'https://')):
        ser = 'ds=nocloud-net;s={};i={}'.format(opts['cloud-init'], uuid)
    else:
        build_cloudinit_image(uuid, opts['cloud-init'])
        ser = f'ds=nocloud;i={uuid}'
        args.extend([
            '-s', '{0}:0,ahci-cd,/cloud-init.iso,ro'.format(CINIT_SLOT)
        ])

if opts['type'] == 'openbsd':
    logging.info('Ignoring unknown MSRs for OpenBSD')
    args.append('-w')

if uuid:
    args.extend(['-U', uuid])

# The ACPI option has no effect with illumos bhyve
#if boolv(opts['acpi'], 'acpi'):
#    args.append('-A')

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

for i, v in build_devlist('cdrom', 8):
    args.extend([
        '-s', '{0}:{1},{2},{3},ro'.format(CDROM_SLOT, i, 'ahci-cd', v)
    ])
    add_bootoption('cdrom', i, ('pci', f'{CDROM_SLOT}.{i}'))

# Bootdisk

try:
    bootdisk = xmlroot.find('./attr[@name="bootdisk"]')
    args.extend([
        '-s', '{0}:0,{1},{2}'.format(BOOTDISK_SLOT, opts['diskif'],
            diskpath(bootdisk.get('value').strip()))
    ])
    add_bootoption('bootdisk', None, ('pci', f'{BOOTDISK_SLOT}.0'))
except:
    pass

# Additional Disks

for i, v in build_devlist('disk', 16):
    if (vv := xmlroot.find(f'./attr[@name="diskif{i}"]')) is not None:
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
for f in xmlroot.findall('./network[@physical]'):
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

    logging.debug(f'Setting promisc-filtered for {nic} to {promisc}')
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
            vncpassword = file_or_string(v)
            del vopts[k]

    opts['vnc'] = collapseopts(vopts)

    args.extend(['-s', '{0}:0,fbuf,vga={1},{2}'.format(
        VNC_SLOT, opts['vga'], opts['vnc'])])
    if boolv(opts['xhci'], 'xhci'):
        args.extend(['-s', '{0}:1,xhci,tablet'.format(VNC_SLOT)])

# PPT - PCI Pass-through devices

pptlist = build_devlist('ppt', 8, False)
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

for i, v in build_devlist('virtfs', 8):
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

if opts['extra']:
    args.extend(opts['extra'].split(' '))

# Dump configuration

args.extend(['-o', 'config.dump=1'])

# VM name

args.append(name)

##############################################################################

logging.debug(f'Final bootoptions:\n{pformat(bootoptions)}')
if uefivars_path and not testmode:
    v = uefivars.UEFIVars(uefivars_path)
    for i in sorted(v.bootmap.keys()):
        logging.debug(f'Boot{i:04x} - {v.bootmap[i]}')
    logging.debug('-----------')
    for k, i in v.bootrmap.items():
        logging.debug(f'{k} -> Boot{i:04x}')
    apply_bootorder(v)
    apply_bootnext(v)
    try:
        v.write()
    except Exception as e:
        logging.info(f'Could not write boot options: {e}')

logging.debug(f'Final arguments:\n{pformat(args)}')
logging.info('{0}'.format(' '.join(map(
    lambda s: f'"{s}"' if ' ' in s else s, args))))

p = subprocess.run(args, capture_output=True, text=True)
# config.dump exits with a status code of 1. Other errors indicate a problem.
if p.returncode != 1:
    fatal(f'Could not parse configuration: {p.stderr}')

fh = None
if not testmode:
    try:
        fh = tempfile.NamedTemporaryFile(mode='w', dir=f'{zoneroot}/etc',
            prefix='bhyve.', delete=False)
    except Exception as e:
        fatal(f'Could not create temporary file: {e}')
    else:
        logging.debug(f'Created temporary file at {fh.name}')

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
        os.rename(tf, f'{zoneroot}/etc/bhyve.cfg')
    except Exception as e:
        fatal(f'Could not create bhyve.cfg from temporary file: {e}')
    else:
        logging.info(f'Successfully created {zoneroot}/etc/bhyve.cfg')

# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
