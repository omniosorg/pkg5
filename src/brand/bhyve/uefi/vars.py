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

import os, shutil, tempfile
from construct import *
from pprint import pprint, pformat
from uuid import UUID
from . import align

setGlobalPrintFullStrings(True)
# setGlobalPrintFalseFlags(True)
# setGlobalPrintPrivateEntries(True)

# The uefi-edk2 nvram firmware volume is divided into sections as follows:
#
#  0x00000 - 0x0dfff     NV_VARIABLE_STORE (Firmware volume)
#  0x0e000 - 0x0efff     NV_EVENT_LOG
#  0x0f000 - 0x0ffff     NV_FTW_WORKING (Fault-tolerant-write)
#  0x10000 - 0x1ffff     NV_FTW_SPARE
#
# This is valid for firmware generated with an FD_SIZE of 1024 or 2048.

VAR_STORE_VOLUME_SIZE = 0xE000

VAR_STORE_FORMATTED = 0x5A
VAR_STORE_HEALTHY = 0xFE

VARIABLE_DATA = 0x55AA

VAR_ADDED = 0x3F
VAR_DELETED = 0xFD
VAR_IN_DELETED_TRANSITION = 0xFE
VAR_HEADER_VALID_ONLY = 0x7F
VAR_ADDED_TRANSITION = VAR_ADDED & VAR_IN_DELETED_TRANSITION
VAR_DELETED_TRANSITION = VAR_ADDED & VAR_DELETED & VAR_IN_DELETED_TRANSITION

GLOBAL_VARIABLE_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"

EfiGuid = Union(
    0,
    "efiguid"
    / Struct(
        "data1" / Hex(Int32ul),
        "data2" / Hex(Int16ul),
        "data3" / Hex(Int16ul),
        "data4" / Array(8, Hex(Int8ul)),
    ),
    "raw" / Bytes(16),
    "str" / Computed(lambda ctx: str(UUID(bytes_le=ctx.raw))),
)

EfiTime = Struct(
    "year" / Int16ul,
    "month" / Int8ul,
    "day" / Int8ul,
    "hour" / Int8ul,
    "min" / Int8ul,
    "sec" / Int8ul,
    "_pad1" / Int8ul,  # padding
    "nanosec" / Int32ul,
    "tz" / Int16ul,
    "daylight" / Int8ul,
    "_pad2" / Int8ul,  # padding
)

BlockMapEntry = Struct(
    "num" / Int32ul,
    "len" / Int32ul,
)

VariableStoreHeader = Struct(
    "guid" / EfiGuid,
    "size" / Int32ul,
    "format" / Const(VAR_STORE_FORMATTED, Int8ul),
    "state" / Const(VAR_STORE_HEALTHY, Int8ul),
    "_rsvd1" / Int16ul,  # reserved
    "_rsvd1" / Int32ul,  # reserved
)

AuthVariable = Struct(
    "offset" / Hex(Tell),
    "startid" / Int16ul,
    "state" / Hex(Int8ul),
    "_rsvd1" / Int8ul,  # reserved
    "attributes"
    / FlagsEnum(
        Int32ul,
        EFI_VARIABLE_NON_VOLATILE=0x00000001,
        EFI_VARIABLE_BOOTSERVICE_ACCESS=0x00000002,
        EFI_VARIABLE_RUNTIME_ACCESS=0x00000004,
        EFI_VARIABLE_HARDWARE_ERROR_RECORD=0x00000008,
        EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS=0x00000020,
        EFI_VARIABLE_APPEND_WRITE=0x00000040,
    ),
    "count" / Int64ul,
    "timestamp" / EfiTime,
    "pubkeyindex" / Int32ul,
    "namelen" / Int32ul,
    "datalen" / Int32ul,
    "guid" / EfiGuid,
    "name" / CString("utf_16_le"),
    "data" / align.Aligned(4, Bytes(this.datalen), pattern=b"\xff"),
    "next" / Peek(Int16ul),
)

Volume = Struct(
    "zero_vector" / Array(2, Int64ul),
    "guid" / EfiGuid,
    "volsize" / Int64ul,
    "signature" / Const(b"_FVH"),
    "attributes" / Int32ul,
    "headerlen" / Int16ul,
    "checksum" / Int16ul,
    "ext_hdr_ofset" / Const(0, Int16ul),
    "_rsvd1" / Int8ul,  # reserved
    "revision" / Const(2, Int8ul),
    "maps" / RepeatUntil(obj_.num == 0 and obj_.len == 0, BlockMapEntry),
    "header" / VariableStoreHeader,
    "next" / Peek(Int16ul),
    StopIf(this.next != VARIABLE_DATA),
    "vars" / RepeatUntil(obj_.next != VARIABLE_DATA, AuthVariable),
)

# UEFI Specification 2.9, section 10.3.1
DevicePath = Struct(
    "type" / Hex(Int8ul),
    "subtype" / Hex(Int8ul),
    "length" / Hex(Int16ul),
    "datalen" / Computed(this.length - 4),
    "data" / Bytes(this.datalen),
)

BootEntry = Struct(
    "attributes"
    / FlagsEnum(
        Int32ul,
        LOAD_OPTION_ACTIVE=0x00000001,
        LOAD_OPTION_FORCE_RECONNECT=0x00000002,
        LOAD_OPTION_HIDDEN=0x00000008,
        LOAD_OPTION_CATEGORY_APP=0x00000100,
    ),
    "fplen" / Int16ul,
    "title" / CString("utf_16_le"),
    "paths"
    / RepeatUntil(obj_.type == 0x7F and obj_.subtype == 0xFF, DevicePath),
    "data" / GreedyRange(Byte),
)


class UEFIVars:
    path = None
    _data = None
    volume = None
    bootmap = None
    bootrmap = None

    def __init__(self, path):
        self.path = path

        with open(path, "rb") as f:
            self._data = f.read(VAR_STORE_VOLUME_SIZE)

        self.volume = Volume.parse(self._data)
        if "vars" not in self.volume:
            self.volume.vars = ListContainer()
        self._parse_bootoptions()

    def _parse_bootoptions(self):
        def be_index(x):
            return int(x.name[4:], 16) if x.name.startswith("Boot0") else 255

        def is_be(x):
            return (
                x.state == VAR_ADDED
                and x.guid.str == GLOBAL_VARIABLE_GUID
                and x.name.startswith("Boot0")
            )

        fmap = {}
        paths = []
        for v in sorted(filter(is_be, self.vars), key=be_index):
            index = be_index(v)
            data = BootEntry.parse(v.data)

            if (
                not data.attributes.LOAD_OPTION_ACTIVE
                or data.attributes.LOAD_OPTION_HIDDEN
            ):
                continue

            guid = pci = path = None
            uri = False
            for p in data.paths:
                if p.type == 1 and p.subtype == 1 and p.datalen == 2:
                    pci = "{1}.{0}".format(*p.data)
                if p.type == 4 and p.subtype == 6 and p.datalen == 16:
                    # App, read GUID
                    guid = EfiGuid.parse(p.data).str
                if p.type == 3 and p.subtype == 24:
                    uri = True
                if p.type == 4 and p.subtype == 4:
                    path = CString("utf_16_le").parse(p.data)

            entry = None
            if pci and uri:
                entry = ("pci", pci, "http")
            elif pci:
                entry = ("pci", pci)
            elif guid:
                entry = ("app", guid)
            elif path:
                entry = ("path", path)
                paths.append(index)

            if entry:
                fmap[index] = entry

        self.bootmap = fmap
        self.bootrmap = {v: k for k, v in fmap.items()}

        for i in fmap.keys():
            self.bootrmap[("boot", i)] = i

        if paths:
            self.bootrmap[("path",)] = paths[0]
            for i, pi in enumerate(paths):
                self.bootrmap[("path", i)] = pi

    def print_vars(self):
        i = 0
        for v in self.vars:
            if v.state == VAR_ADDED:
                s = "   "
            else:
                s = "DEL"
            print(f"[{i:2}] {s} {v.name} size {v.datalen}")
            i += 1

    def _find_var(self, name, guid):
        """Looks for a variable with the provided 'name' and 'guid'.
        This function will return the active variable if it exists,
        otherwise it will return the last found variable, which may be
        in the deleted state. If no variable is found, a new one will
        be created, initialised with defaults and added to the in-memory
        variable list."""
        last = None
        for v in self.vars:
            if v.name != name or v.guid.str != guid:
                continue
            if v.state == VAR_ADDED:
                # Found an active instance of this variable
                return v
            else:
                last = v
        if last:
            return last

        # Build new variable
        v = AuthVariable.parse(b"\x00" * 0x100)
        v.startid = VARIABLE_DATA
        v.state = VAR_ADDED
        v.attributes.EFI_VARIABLE_NON_VOLATILE = True
        v.attributes.EFI_VARIABLE_BOOTSERVICE_ACCESS = True
        v.attributes.EFI_VARIABLE_RUNTIME_ACCESS = True
        v.name = name
        v.namelen = (len(name) + 1) * 2
        v.guid = EfiGuid.parse(UUID(guid).bytes_le)

        # The current last variable needs flagging that it is no-longer the
        # last.
        try:
            self.vars[-1].next = VARIABLE_DATA
        except IndexError:
            pass

        self.vars.append(v)

        return v

    @property
    def vars(self):
        return self.volume.vars

    def defrag(self):
        # To de-fragment a variables file, one needs to:
        # - preserve all VAR_ADDED variables
        # - promote VAR_IN_DELETED_TRANSITION variables to VAR_ADDED providing
        #   there is not already a VAR_ADDED variable with the same GUID and
        #   name.

        # Build a list of existing VAR_ADDED variables
        added = [
            f"{v.guid.str}/{v.name}" for v in self.vars if v.state == VAR_ADDED
        ]

        vars = [
            v
            for v in self.vars
            if v.state == VAR_ADDED
            or (
                v.state == (VAR_ADDED & VAR_IN_DELETED_TRANSITION)
                and f"{v.guid.str}/{v.name}" not in added
            )
        ]

        # Now promote any remaining ADDED/IN_DELETED_TRANSITION entries
        for v in vars:
            v.state = VAR_ADDED

        # Mark the new last element as the terminator
        self.vars[-1].next = 0xFFFF

        self.volume.vars = ListContainer(vars)

    def write_volume(self, fh):
        shutil.copy2(self.path, fh.name)
        fh.seek(0)
        fh.write(Volume.build(self.volume))

        return VAR_STORE_VOLUME_SIZE - fh.tell()

    def write(self, path=None):
        if not path:
            path = self.path
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=os.path.dirname(self.path),
            prefix="uefivars.",
            delete=False,
        ) as fh:
            pad = self.write_volume(fh)
            if pad < 0:
                # Variable store overflow into event log section.
                self.defrag()
                pad = self.write_volume(fh)

            if pad < 0:
                raise OverflowError("Variable store too large")
            fh.write(b"\xff" * pad)

            tf = fh.name

        os.rename(tf, path)

    def set_bootorder(self, options):
        order = []
        for opt in options:
            try:
                opt = self.bootrmap[opt]
            except:
                pass
            else:
                order.append(opt)
        if not len(order):
            raise KeyError

        v = self._find_var("BootOrder", GLOBAL_VARIABLE_GUID)

        v.state = VAR_ADDED
        v.data = Array(len(order), Int16ul).build(order)
        v.datalen = len(v.data)

    def set_bootnext(self, opt):
        opt = self.bootrmap[opt]  # can raise KeyError

        v = self._find_var("BootNext", GLOBAL_VARIABLE_GUID)

        v.state = VAR_ADDED
        v.data = Int16ul.build(opt)
        v.datalen = len(v.data)


# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
