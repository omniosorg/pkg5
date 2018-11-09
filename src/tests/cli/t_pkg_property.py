#!/usr/bin/python3
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
# Copyright (c) 2008, 2016, Oracle and/or its affiliates. All rights reserved.
# Copyright 2018 OmniOS Community Edition (OmniOSce) Association.
#

from . import testutils
if __name__ == "__main__":
        testutils.setup_environment("../../../proto")
import pkg5unittest

import os
import errno
import pkg.portable as portable
import unittest


class TestPkgPropertyBasics(pkg5unittest.SingleDepotTestCase):
        # Only start/stop the depot once (instead of for every test)
        persistent_setup = True

        def test_pkg_properties(self):
                """pkg: set, unset, and display properties"""

                self.image_create(self.rurl)

                self.pkg("set-property -@", exit=2)
                self.pkg("get-property -@", exit=2)
                self.pkg("property -@", exit=2)

                self.pkg("set-property title sample")
                self.pkg('set-property description "more than one word"')
                self.pkg("property")
                self.pkg("property | grep title")
                self.pkg("property | grep description")
                self.pkg("property | grep 'sample'")
                self.pkg("property | grep 'more than one word'")
                self.pkg("unset-property description")
                self.pkg("property -H")
                self.pkg("property title")
                self.pkg("property -H title")
                self.pkg("property description", exit=1)
                self.pkg("unset-property description", exit=1)
                self.pkg("unset-property", exit=2)

                self.pkg("set-property signature-policy verify")
                self.pkg("set-property signature-policy verify foo", exit=2)
                self.pkg("set-property signature-policy vrify", exit=1)
                self.pkg("set-property signature-policy require-names", exit=1)
                self.pkg("set-property signature-policy require-names foo")

                self.pkg("add-property-value signature-policy verify", exit=1)
                self.pkg("add-property-value signature-required-names foo")
                self.pkg("add-property-value signature-required-names bar")
                self.pkg("remove-property-value signature-required-names foo")
                self.pkg("remove-property-value signature-required-names baz",
                    exit=1)
                self.pkg("add-property-value foo", exit=2)
                self.pkg("remove-property-value foo", exit=2)
                self.pkg("set-property foo", exit=2)
                self.pkg("set-property foo bar")
                self.pkg("remove-property-value foo bar", exit=1)
                self.pkg("set-property", exit=2)

                self.pkg("set-property trust-anchor-directory {0} {1}".format(
                    self.test_root, self.test_root), exit=1)

                # Verify that properties with single values can be set and
                # retrieved as expected.
                self.pkg("set-property flush-content-cache-on-success False")
                self.pkg("property -H flush-content-cache-on-success |"
                    "grep -i flush-content-cache-on-success.*false$")

        def test_missing_permissions(self):
                """Bug 2393"""

                self.image_create(self.rurl)

                self.pkg("property")
                self.pkg("set-property require-optional True", su_wrap=True,
                    exit=1)
                self.pkg("set-property require-optional True")
                self.pkg("unset-property require-optional", su_wrap=True,
                    exit=1)
                self.pkg("unset-property require-optional")

        foo10 = """
            open foo@1.0,5.11-0
            add dir mode=0755 owner=root group=bin path=/lib
            close """

        foo11 = """
            open foo@1.1,5.11-0
            add dir mode=0755 owner=root group=bin path=/lib
            close """

        def test_pkg_property_keyfiles(self):
                """key-files image property"""

                self.pkgsend_bulk(self.rurl, [self.foo10, self.foo11])

                vanilla = "lib/.vanilla"
                pecan = "lib/.pecan"

                self.image_create(self.rurl)
                self.pkg("install foo@1.0")

                self.pkg("add-property-value key-files lib/.vanilla")
                # Even adding a new keyfile property will fail as the
                # image configuration cannot be loaded with a missing key-file.
                self.pkg("add-property-value key-files lib/.pecan", exit=51)
                self.file_touch(vanilla)
                self.pkg("add-property-value key-files lib/.pecan")
                self.file_touch(pecan)

                self.pkg("property key-files")
                self.assertTrue("vanilla" in self.output)

                # pkg update should fail due to missing keyfile
                self.file_remove(vanilla)
                self.pkg("update", exit=51)
                self.assertTrue("Is everything mounted" in self.errout)

                # and now succeed
                self.file_touch(vanilla)
                self.pkg("update")

        xpkg = """
    open xpkg@1.0,5.11-0
    add dir mode=0755 owner=root group=bin path=/usr
    add dir mode=0755 owner=root group=bin path=/usr/lib
    add dir mode=0755 owner=root group=bin path=/usr/lib/fm
    add dir mode=0755 owner=root group=bin path=/sbin
    add file tmp/cat mode=0644 owner=sys group=sys path=/bambam
    add file tmp/cat mode=0644 owner=sys group=sys path=/sbin/dino
    add file tmp/cat mode=0644 owner=sys group=sys path=/usr/lib/libfred.so
    add file tmp/cat mode=0644 owner=sys group=sys path=/usr/lib/libbarney.so
    add file tmp/cat mode=0644 owner=sys group=sys path=/usr/lib/fm/wilma.xml
    add file tmp/cat mode=0644 owner=sys group=sys path=/usr/lib/fm/betty.xml
    close
        """

        misc_files = [ 'tmp/cat' ]

        def test_pkg_property_excludes(self):
                """exclude-patterns image property"""

                self.make_misc_files(self.misc_files)

                self.pkgsend_bulk(self.rurl, self.xpkg)

                self.image_create(self.rurl)
                self.pkg("add-property-value exclude-patterns 'usr/(?!lib/fm)'")
                self.pkg("add-property-value exclude-patterns 'sbin/'")

                self.pkg("install xpkg")

                self.assert_files_exist((
                    ("bambam", True),
                    ("sbin/dino", False),
                    ("usr/lib/libfred.so", False),
                    ("usr/lib/libbarney.so", False),
                    ("usr/lib/fm/wilma.xml", True),
                    ("usr/lib/fm/betty.xml", True),
                ))


if __name__ == "__main__":
        unittest.main()

# Vim hints
# vim:ts=8:sw=8:et:fdm=marker
