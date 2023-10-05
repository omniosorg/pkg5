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
# Copyright (c) 2011, 2016, Oracle and/or its affiliates. All rights reserved.
# Copyright 2021 OmniOS Community Edition (OmniOSce) Association.
#

from . import testutils

if __name__ == "__main__":
    testutils.setup_environment("../../../proto")
import pkg5unittest

import os
import stat
import time

import pkg.client.api_errors as apx
import pkg.json as json
import pkg.fmri as fmri


class TestPkgFlag(pkg5unittest.SingleDepotTestCase):
    persistent_setup = True

    foo10 = """
            open foo@1.0,5.11-0
            close """

    bar10 = """
            open bar@1.0,5.11-0
            close """

    baz10 = """
            open baz@1.0,5.11-0
            close """

    def setUp(self):
        pkg5unittest.SingleDepotTestCase.setUp(self)
        self.sent_pkgs = self.pkgsend_bulk(
            self.rurl, [self.foo10, self.bar10, self.baz10]
        )
        self.foo10_name = fmri.PkgFmri(self.sent_pkgs[0]).get_fmri(anarchy=True)
        self.bar10_name = fmri.PkgFmri(self.sent_pkgs[1]).get_fmri(anarchy=True)
        self.baz_name = fmri.PkgFmri(self.sent_pkgs[2]).get_fmri(anarchy=True)

    def test_bad_input(self):
        """Test bad options to pkg flag."""

        self.api_obj = self.image_create(self.rurl)

        self.pkg("flag", exit=2)
        # Unknown flag
        self.pkg("flag -c", exit=2)
        self.pkg("flag -c 'foo'", exit=2)
        self.pkg("flag pkg://foo", exit=2)
        # Cannot specify both -m and -M
        self.pkg("flag -m -M foo", exit=1)
        # Must specify at least one package
        self.pkg("flag -m", exit=2)

    def test_flag_m(self):
        """Test flag -m/M"""

        self.api_obj = self.image_create(self.rurl)

        self._api_install(self.api_obj, ["bar", "baz"])
        os.environ["PKG_AUTOINSTALL"] = "1"
        self._api_install(self.api_obj, ["foo"])
        del os.environ["PKG_AUTOINSTALL"]

        expected = self.reduceSpaces(
            "bar    1.0-0    im-\n"
            "baz    1.0-0    im-\n"
            "foo    1.0-0    i--\n"
        )

        self.pkg("list -H")
        output = self.reduceSpaces(self.output)
        self.assertEqualDiff(expected, output)

        self.pkg("flag -M bar")
        expected = self.reduceSpaces(
            "bar    1.0-0    i--\n"
            "baz    1.0-0    im-\n"
            "foo    1.0-0    i--\n"
        )
        self.pkg("list -H")
        output = self.reduceSpaces(self.output)
        self.assertEqualDiff(expected, output)

        self.pkg("flag -M '*'")
        expected = self.reduceSpaces(
            "bar    1.0-0    i--\n"
            "baz    1.0-0    i--\n"
            "foo    1.0-0    i--\n"
        )
        self.pkg("list -H")
        output = self.reduceSpaces(self.output)
        self.assertEqualDiff(expected, output)

        self.pkg("flag -m foo")
        expected = self.reduceSpaces(
            "bar    1.0-0    i--\n"
            "baz    1.0-0    i--\n"
            "foo    1.0-0    im-\n"
        )
        self.pkg("list -H")
        output = self.reduceSpaces(self.output)
        self.assertEqualDiff(expected, output)

        self.pkg("flag -m '*'")
        expected = self.reduceSpaces(
            "bar    1.0-0    im-\n"
            "baz    1.0-0    im-\n"
            "foo    1.0-0    im-\n"
        )
        self.pkg("list -H")
        output = self.reduceSpaces(self.output)
        self.assertEqualDiff(expected, output)

        self.pkg("uninstall bar")
        expected = self.reduceSpaces(
            "baz    1.0-0    im-\n" "foo    1.0-0    im-\n"
        )
        self.pkg("list -H")
        output = self.reduceSpaces(self.output)
        self.assertEqualDiff(expected, output)

        # Check that the 'known' catalogue is still consistent
        expected = self.reduceSpaces(
            "bar    1.0-0    ---\n"
            "baz    1.0-0    im-\n"
            "foo    1.0-0    im-\n"
        )
        self.pkg("list -Ha")
        output = self.reduceSpaces(self.output)
        self.assertEqualDiff(expected, output)


# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
