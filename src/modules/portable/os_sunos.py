#!/usr/bin/python3.5
#
# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
# or http://www.opensolaris.org/os/licensing.
# See the License for the specific language governing permissions
# and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at usr/src/OPENSOLARIS.LICENSE.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END
#
# Copyright (c) 2008, 2016, Oracle and/or its affiliates. All rights reserved.
# Copyright 2017 Lauri Tirkkonen <lotheac@iki.fi>
# Copyright 2018 OmniOS Community Edition (OmniOSce) Association.
#

"""
Most of the generic unix methods of our superclass can be used on
Solaris. For the following methods, there is a Solaris-specific
implementation in the 'arch' extension module.
"""

import os
import six
import subprocess
import tempfile

from .os_unix import \
    get_group_by_name, get_user_by_name, get_name_by_gid, get_name_by_uid, \
    get_usernames_by_gid, is_admin, get_userid, get_username, chown, rename, \
    remove, link, copyfile, split_path, get_root, assert_mode
from pkg.portable import ELF, EXEC, PD_LOCAL_PATH, UNFOUND, SMF_MANIFEST

import pkg.arch as arch
from pkg.sysattr import fgetattr, fsetattr
from pkg.sysattr import get_attr_dict as get_sysattr_dict

def get_isainfo():
        return arch.get_isainfo()

def get_release():
        return arch.get_release()

def get_platform():
        return arch.get_platform()

def get_file_type(actions):
        from pkg.flavor.smf_manifest import is_smf_manifest
        for a in actions:
                lpath = a.attrs[PD_LOCAL_PATH]
                if os.stat(lpath).st_size == 0:
                        # Some tests rely on this being identified
                        yield "empty file"
                        continue
                try:
                        with open(lpath, 'rb') as f:
                                magic = f.read(4)
                except FileNotFoundError:
                        yield UNFOUND
                        continue
                if magic == b'\x7fELF':
                        yield ELF
                elif magic[:2] == b'#!':
                        yield EXEC
                elif lpath.endswith('.xml'):
                        if is_smf_manifest(lpath):
                                yield SMF_MANIFEST
                        else:
                                # Some tests rely on this type being identified
                                yield "XML document"
                else:
                        yield "unknown"

# Vim hints
# vim:ts=8:sw=8:et:fdm=marker
