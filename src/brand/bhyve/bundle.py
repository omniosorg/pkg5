#!/usr/bin/python3.11 -Es

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

from site import addsitedir
import os, sys, platform

# If PYTHONPATH is set in the environment and the environment is not
# being ignored, then don't adjust the path.
if "PYTHONPATH" not in os.environ or getattr(sys.flags, "ignore_environment"):
    sys.path, remainder = sys.path[:2], sys.path[2:]
    addsitedir(
        "{}/python{}".format(
            os.path.dirname(__file__),
            ".".join(platform.python_version_tuple()[:2]),
        )
    )
    sys.path.extend(remainder)

# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
