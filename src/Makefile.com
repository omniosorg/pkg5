#
# This file and its contents are supplied under the terms of the
# Common Development and Distribution License ("CDDL"), version 1.0.
# You may only use this file in accordance with the terms of version
# 1.0 of the CDDL.
#
# A full copy of the text of the CDDL should have accompanied this
# source.  A copy of the CDDL is also available via the Internet at
# http://www.illumos.org/license/CDDL.
#

# Copyright 2022, Richard Lowe.
# Copyright 2022 OmniOS Community Edition (OmniOSce) Association.

CODE_WS:sh = git rev-parse --show-toplevel
MACH:sh = uname -p
PROTO = $(CODE_WS)/proto

ROOT = $(PROTO)/root_$(MACH)
ROOTETC = $(ROOT)/etc
ROOTETCZONES = $(ROOTETC)/zones
ROOTETCBRAND = $(ROOTETC)/brand
ROOTUSRLIB = $(ROOT)/usr/lib
ROOTBRAND = $(ROOTUSRLIB)/brand
ROOTPKGLIB = $(ROOTUSRLIB)/pkg

CC = /usr/bin/gcc-13
CFLAGS = -m64 -Wall -Werror -Wextra -gdwarf-2 -gstrict-dwarf \
	-fno-aggressive-loop-optimizations
CPPFLAGS = -D_REENTRANT -D_POSIX_PTHREAD_SEMANTICS

# Whitespace separated list of versions to build and test, latest one first
PYVERSIONS = 3.11
# The single version used for shebang lines and packaging.
PYVER = 3.11

SHELL= /usr/bin/ksh93
INSTALL = /usr/sbin/install
CTFCONVERT = /opt/onbld/bin/i386/ctfconvert
STRIP = /usr/bin/strip

CTFCONVERT_BIN = $(CTFCONVERT) -l pkg5
POST_PROCESS = $(CTFCONVERT_BIN) $@; $(STRIP) -x $@

PRE_HASH=	pre\#
HASH=		$(PRE_HASH:pre\%=%)

