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
# Copyright 2024 OmniOS Community Edition (OmniOSce) Association.

CODE_WS:sh = git rev-parse --show-toplevel
MACH:sh = uname -p
PROTO = $(CODE_WS)/proto

ROOT = $(PROTO)/root_$(MACH)
ROOTETC = $(ROOT)/etc
ROOTETCZONES = $(ROOTETC)/zones
ROOTETCBRAND = $(ROOTETC)/brand
ROOTUSRLIB = $(ROOT)/usr/lib
ROOTUSRBIN = $(ROOT)/usr/bin
ROOTUSRSHARE = $(ROOT)/usr/share
ROOTUSRSHARELOCALE = $(ROOTUSRSHARE)/locale
ROOTBRAND = $(ROOTUSRLIB)/brand
ROOTPKGLIB = $(ROOTUSRLIB)/pkg
TRIPLET = x86_64-pc-solaris2

CC = /usr/bin/gcc-14
CFLAGS_i386 = -m64
CFLAGS_aarch64 =
CFLAGS = $(CFLAGS_$(MACH)) -Wall -Werror -Wextra -gdwarf-2 -gstrict-dwarf \
	-fno-aggressive-loop-optimizations
CPPFLAGS = -D_REENTRANT -D_POSIX_PTHREAD_SEMANTICS

# Whitespace separated list of versions to build and test.
PYVERSIONS = 3.13
# The single version used for shebang lines and packaging.
PYVER = 3.13

SHELL= /usr/bin/ksh93
INSTALL = /usr/sbin/install
CTFCONVERT = /opt/onbld/bin/i386/ctfconvert
STRIP = /usr/bin/strip
RM = /usr/bin/rm -f
MV = /usr/bin/mv
MKDIR =	/usr/bin/mkdir -p
RMDIR = /usr/bin/rmdir
SED = /usr/bin/sed
CHMOD = /usr/bin/chmod

CTFCONVERT_BIN = $(CTFCONVERT) -l pkg5
POST_PROCESS = $(CTFCONVERT_BIN) $@; $(STRIP) -x $@
PYCOMPILE_OPTS = -m compileall -j0 -f --invalidation-mode timestamp

PRE_HASH=	pre\#
HASH=		$(PRE_HASH:pre\%=%)

