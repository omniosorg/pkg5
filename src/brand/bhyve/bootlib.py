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

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as etree
import uuid as uuidlib
from pprint import pformat

log_quiet = False


def log_stdout(level):
    logging.basicConfig(stream=sys.stdout, level=level)


def log_file(file, level):
    os.makedirs(os.path.dirname(file), mode=0o711, exist_ok=True)
    logging.basicConfig(filename=file, filemode="a", level=level, force=True)


def set_quiet(val):
    global log_quiet
    log_quiet = val


def fatal(msg):
    logging.error(msg)
    print(msg, file=sys.stderr)
    sys.exit(1)


def error(msg):
    logging.error(msg)


def debug(msg):
    if not log_quiet:
        logging.debug(msg)


def info(msg):
    if not log_quiet:
        logging.info(msg)


def warning(msg):
    if not log_quiet:
        logging.warning(msg)


##############################################################################


class Zone:
    xmlfile = None
    xmlcfg = None
    xmlroot = None

    def __init__(self, xmlfile):
        self.xmlfile = xmlfile

        if not os.path.isfile(xmlfile):
            fatal(f"Cannot find zone XML file at {xmlfile}")
        try:
            self.xmlcfg = etree.parse(xmlfile)
        except:
            fatal(f"Could not parse {xmlfile}")

        self.xmlroot = self.xmlcfg.getroot()

    def __getattr__(self, attr):
        return self.xmlroot.get(attr)

    @property
    def zoneroot(self):
        return f"{self.zonepath}/root"

    def find(self, path):
        return self.xmlroot.find(path)

    def findall(self, path):
        return self.xmlroot.findall(path)

    def findattr(self, attr, all=False):
        if all:
            # Note that zonecfg will not allow the creation of multiple
            # attributes with the same name, but some third-party zone
            # management tools are not as strict.
            return self.findall(f'./attr[@name="{attr}"]')
        else:
            return self.find(f'./attr[@name="{attr}"]')

    def iterattr(self, regex):
        for dev in self.findall("./attr[@name]"):
            if m := re.search(regex, dev.get("name").strip()):
                yield dev, m

    def parseopt(self, tag, opts, aliases):
        try:
            el = self.findattr(tag)
            opts[tag] = el.get("value").strip()
            debug(f'Found custom {tag} attribute - "{opts[tag]}"')
            if tag in aliases:
                val = opts[tag]
                if (bt := boolv(val, tag, ignore=True)) is not None:
                    val = "on" if bt else "off"
                try:
                    opts[tag] = aliases[tag][val]
                    debug(f"  Alias expanded to {opts[tag]}")
                except KeyError:
                    pass
        except:
            pass

    def uuid(self):
        try:
            with open(f"{self.zoneroot}/etc/uuid") as file:
                uuid = file.read().strip()
                info(f"Read zone UUID: {uuid}")
        except FileNotFoundError:
            uuid = str(uuidlib.uuid4())
            info(f"Generated UUID: {uuid}")
        except Exception as e:
            fatal(f"Could not read zone UUID file: {e}")
        return uuid

    # Look for attributes of the form <type>N (and <type> if plain is True) and
    # generate a list.
    def build_devlist(self, type, maxval, plain=True):
        devlist = {}
        for dev, m in self.iterattr(rf"^{type}(\d+)$"):
            k = int(m.group(1))
            if k in devlist:
                fatal(f"{type}{k} appears more than once in configuration")
            if k >= maxval:
                fatal(f"{type}{k} slot out of range")
            devlist[k] = dev.get("value").strip()

        if plain:
            # Now insert plain <type> tags into the list, using available slots
            # in order
            avail = sorted(
                set(range(0, maxval)).difference(sorted(devlist.keys()))
            )
            for dev in self.findattr(type, True):
                try:
                    k = avail.pop(0)
                except IndexError:
                    fatal(f"{type}: no more available slots")
                devlist[k] = dev.get("value").strip()

        debug("{} list: \n{}".format(type, pformat(devlist)))

        return sorted(devlist.items())

    def build_cloudinit_image(self, uuid, src, testmode):
        info("Generating cloud-init data")

        #### Metadata

        meta_data = {
            "instance-id": uuid,
            "local-hostname": self.name,
        }

        #### Userdata

        user_data = {
            "hostname": self.name,
            "disable_root": False,
        }

        if (v := self.findattr("password")) is not None:
            user_data["password"] = file_or_string(v.get("value"))
            user_data["chpasswd"] = {"expire": False}
            user_data["ssh_pwauth"] = True

        if (v := self.findattr("sshkey")) is not None:
            v = file_or_string(v.get("value"))
            user_data["ssh_authorized_keys"] = [v]
            user_data["users"] = [
                "default",
                {"name": "root", "ssh_authorized_keys": [v]},
            ]

        #### Network

        network_data = {}

        addresses = self.findall("./network[@allowed-address]")
        if len(addresses) > 0:
            nsdone = False
            network_data["version"] = 2
            network_data["ethernets"] = {}

            for a in addresses:
                vnic = a.get("physical")
                addr = a.get("allowed-address")
                rtr = a.get("defrouter")

                mac = get_mac(vnic)
                if mac is None:
                    continue

                data = {
                    "match": {"macaddress": mac},
                    "set-name": vnic,
                    "addresses": [addr],
                }
                if rtr:
                    data["routes"] = [
                        {
                            "to": "0.0.0.0/0",
                            "via": rtr,
                        }
                    ]

                if not nsdone:
                    domain = self.findattr("dns-domain")
                    resolvers = self.findattr("resolvers")
                    if resolvers is not None or domain is not None:
                        nsdata = {}
                        if domain is not None:
                            nsdata["search"] = [domain.get("value").strip()]
                        if resolvers is not None:
                            nsdata["addresses"] = (
                                resolvers.get("value").strip().split(",")
                            )
                        data["nameservers"] = nsdata
                    nsdone = True

                network_data["ethernets"][vnic] = data

        import yaml

        debug("Metadata:\n" + yaml.dump(meta_data))
        debug("Userdata:\n" + yaml.dump(user_data))
        debug("Netdata:\n" + yaml.dump(network_data))

        if testmode:
            return

        dir = tempfile.mkdtemp(dir=f"{self.zoneroot}", prefix="cloud-init.")

        with open(f"{dir}/meta-data", "w") as fh:
            yaml.dump(meta_data, fh)

        if os.path.isabs(src) and os.path.isfile(src):
            info(f"Using supplied cloud-init user-data file from {src}")
            shutil.copyfile(src, f"{dir}/user-data")
        else:
            with open(f"{dir}/user-data", "w") as fh:
                fh.write("#cloud-config\n")
                yaml.dump(user_data, fh)

        if network_data:
            with open(f"{dir}/network-config", "w") as fh:
                yaml.dump(network_data, fh)

        #### Build image

        cidir = f"{self.zoneroot}/cloud-init"
        seed = f"{self.zoneroot}/cloud-init.iso"
        if os.path.exists(cidir):
            shutil.rmtree(cidir)
        os.rename(dir, cidir)
        info("Building cloud-init ISO image")
        try:
            ret = subprocess.run(
                [
                    "/usr/bin/mkisofs",
                    "-full-iso9660-filenames",
                    "-untranslated-filenames",
                    "-rock",
                    "-volid",
                    "CIDATA",
                    "-o",
                    seed,
                    cidir,
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            for l in ret.stdout.splitlines():
                info(l)
            os.chmod(seed, mode=0o644)
        except Exception as e:
            fatal(f"Could not create cloud-init ISO image: {e}")


##############################################################################


def boolv(s, var, ignore=False):
    if s in ["true", "yes", "on", "1"]:
        return True
    if s in ["false", "no", "off", "0"]:
        return False
    if not ignore:
        fatal(f"Invalid value {s} for boolean variable {var}")
    return None


def file_or_string(f):
    if os.path.isabs(f) and os.path.isfile(f):
        try:
            with open(f) as fh:
                f = fh.read()
        except Exception as e:
            fatal(f"Could not read from {f}: {e}")
    return f.strip()


def expandopts(opt):
    """Expand a comma-separated option string out into a dictionary.
    For example:
        on,password=fred,wait,w=1234
    becomes:
        {'on': '', 'password': 'fred', 'wait': '', 'w': '1234'}
    """
    return {
        k: v for (k, v, *_) in [(o + "=").split("=") for o in opt.split(",")]
    }


def collapseopts(opts):
    """The reverse of expandopts. Convert a dictionary back into an option
    string."""
    return ",".join([f"{k}={v}".rstrip("=") for k, v in opts.items()])


def diskpath(arg):
    if arg.startswith("/"):
        return arg
    return "/dev/zvol/rdsk/{0}".format(arg)


ram_shift = {"e": 60, "p": 50, "t": 40, "g": 30, "m": 20, "k": 10, "b": 0}


def parse_ram(v):
    # Parse a string representing an amount of RAM into bytes
    m = re.search(rf"^(\d+)(.?)$", v)
    if not m:
        fatal(f'Could not parse ram value "{v}"')
    (mem, suffix) = m.groups()
    mem = int(mem)

    if not suffix:
        # If the memory size specified as a plain number is less than a
        # mebibyte then interpret it as being in units of MiB.
        suffix = "m" if mem < MiB else "b"

    try:
        shift = ram_shift[suffix.lower()]
    except KeyError:
        fatal(f"Unknown RAM suffix, {suffix}")

    mem <<= shift

    debug(f"parse_ram({v}) = {mem}")
    return mem


def get_mac(ifname):
    p = subprocess.run(
        ["/usr/sbin/dladm", "show-vnic", "-p", "-o", "macaddress", ifname],
        stdout=subprocess.PIPE,
    )
    if p.returncode != 0:
        warning(f"Could not find MAC address for VNIC {ifname}")
        return None
    mac = p.stdout.decode("utf-8").strip()
    # Need to zero-pad the bytes
    return ":".join(l.zfill(2) for l in mac.split(":"))


# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
