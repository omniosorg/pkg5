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
# Copyright (c) 2019, Oracle and/or its affiliates. All rights reserved.
# Copyright 2022 OmniOS Community Edition (OmniOSce) Association.
#

"""Adjust sys.path so that /usr/lib/pkg is first and that neither the system
   site-packages nor any user site-packages directory is present.
   This provides a stable and consistent execution environment and allows
   bundling some modules with pkg.
"""

from site import getsitepackages, getusersitepackages, addsitedir
import os, sys


def strip_zip():
    sys.path = [d for d in sys.path if not d.endswith(".zip")]


def strip_site():
    strip = getsitepackages()
    strip.append(getusersitepackages())
    sys.path = [d for d in sys.path if d not in strip]


def add_pkglib():
    # If PYTHONPATH is set in the environment and the environment is not
    # being ignored, then don't adjust the path. This could, for example,
    # be running under the testsuite.
    if "PYTHONPATH" in os.environ and not getattr(
        sys.flags, "ignore_environment"
    ):
        return
    import platform

    strip_zip()
    sys.path, remainder = sys.path[:2], sys.path[2:]
    addsitedir(
        "/usr/lib/pkg/python{}".format(
            ".".join(platform.python_version_tuple()[:2])
        )
    )
    sys.path.extend(remainder)


def init():
    strip_site()
    add_pkglib()


# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
