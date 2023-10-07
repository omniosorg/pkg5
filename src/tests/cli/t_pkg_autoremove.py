#!/usr/bin/python3
# -*- coding: utf-8 -*-
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

#
# Copyright (c) 2008, 2020, Oracle and/or its affiliates.
# Copyright 2021 OmniOS Community Edition (OmniOSce) Association.
#

from . import testutils

if __name__ == "__main__":
    testutils.setup_environment("../../../proto")
import pkg5unittest

import errno
import hashlib
import os
import platform
import re
import shutil
import socket
import subprocess
import stat
import struct
import sys
import tempfile
import time
import unittest
import locale

import pkg.actions
import pkg.digest as digest
import pkg.fmri as fmri
import pkg.manifest as manifest
import pkg.misc as misc
import pkg.portable as portable

from pkg.client.pkgdefs import EXIT_OOPS, EXIT_NOP, EXIT_BADOPT


class TestPkgAutoremove(pkg5unittest.SingleDepotTestCase):
    # Only start/stop the depot once (instead of for every test)
    persistent_setup = True

    foo10 = """
            open foo@1.0,5.11-0
            close """

    foo11 = """
            open foo@1.1,5.11-0
            close """

    bar10 = """
            open bar@1.0,5.11-0
            add dir mode=0755 owner=root group=bin path=/bin
            add file tmp/cat mode=0555 owner=root group=bin path=/bin/cat
            close """

    bar11 = """
            open bar@1.1,5.11-0
            add depend type=require fmri=pkg:/foo
            add dir mode=0755 owner=root group=bin path=/bin
            add file tmp/cat mode=0555 owner=root group=bin path=/bin/cat
            close """

    misc_files = ["tmp/cat"]

    def setUp(self):
        pkg5unittest.SingleDepotTestCase.setUp(self)
        self.make_misc_files(self.misc_files)

    def test_cli_badoptions(self):
        """Test bad cli options"""

        self.image_create(self.rurl)

        self.pkg("autoremove -@", exit=EXIT_BADOPT)
        self.pkg("autoremove -vq foo", exit=EXIT_BADOPT)
        self.pkg("autoremove foo@x.y", exit=EXIT_BADOPT)
        self.pkg("autoremove pkg:/foo@bar.baz", exit=EXIT_BADOPT)

    def test_autoremove_basics(self):
        plist = self.pkgsend_bulk(self.rurl, self.foo10)
        self.image_create(self.rurl)

        self.pkg("install --parsable=0 foo")
        self.assertEqualParsable(self.output, add_packages=plist)

        # autoremove should return NOP since there is nothing to do
        self.pkg("autoremove", exit=EXIT_NOP)

        # Now unflag the package as manually installed
        self.pkg("flag -M foo")

        # Autoremove should now remove foo
        self.pkg("autoremove --parsable=0")
        self.assertEqualParsable(self.output, remove_packages=plist)

    def test_autoremove_install_dep(self):
        plist = self.pkgsend_bulk(self.rurl, [self.bar11, self.foo10])
        self.image_create(self.rurl)

        # bar depends on foo
        self.pkg("install --parsable=0 bar")
        self.assertEqualParsable(self.output, add_packages=plist)

        # autoremove should return NOP
        self.pkg("autoremove", exit=EXIT_NOP)

        # Now remove bar
        self.pkg("uninstall bar")

        # Autoremove should now remove foo
        self.pkg("autoremove --parsable=0")
        self.assertEqualParsable(self.output, remove_packages=[plist[1]])

    def test_autoremove_update(self):
        plist = self.pkgsend_bulk(
            self.rurl, [self.bar10, self.bar11, self.foo10]
        )
        self.image_create(self.rurl)

        # bar10 has no dependencies, bar@1.1 depends on foo
        self.pkg("install --parsable=0 bar@1.0")
        self.assertEqualParsable(self.output, add_packages=[plist[0]])

        # This update will pull in foo as a dependency
        self.pkg("update bar")

        # autoremove should return NOP since foo has a dependant
        self.pkg("autoremove", exit=EXIT_NOP)

        # Now remove bar
        self.pkg("uninstall bar")

        # Autoremove should now remove foo
        self.pkg("autoremove --parsable=0")
        self.assertEqualParsable(self.output, remove_packages=[plist[2]])


if __name__ == "__main__":
    unittest.main()

# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
