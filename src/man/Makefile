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

# Copyright 2024 OmniOS Community Edition (OmniOSce) Association.

include ../Makefile.com

ROOTUSRSHAREMAN = $(ROOTUSRSHARE)/man
MAN1DIR = $(ROOTUSRSHAREMAN)/man1
MAN7DIR = $(ROOTUSRSHAREMAN)/man7
MAN8DIR = $(ROOTUSRSHAREMAN)/man8

MAN1 = \
	pkg.1 \
	pkgdepend.1 \
	pkgdiff.1 \
	pkgfmt.1 \
	pkglint.1 \
	pkgmerge.1 \
	pkgmogrify.1 \
	pkgrecv.1 \
	pkgrepo.1 \
	pkgsend.1 \
	pkgsign.1 \
	pkgsurf.1

MAN7 = \
	bhyve.7 \
	emu.7 \
	illumos.7 \
	ipkg.7 \
	kvm.7 \
	lipkg.7 \
	lx.7 \
	pkg.7 \
	pkgsrc.7 \
	sparse.7

MAN8 = \
	pkg.depotd.8

all:

install: \
	$(MAN1DIR) $(MAN7DIR) $(MAN8DIR) \
	$(MAN1:%=$(MAN1DIR)/%) \
	$(MAN7:%=$(MAN7DIR)/%) \
	$(MAN8:%=$(MAN8DIR)/%)

check:
	-mandoc -T lint $(MAN1) $(MAN7) $(MAN8) 2>&1 | \
		egrep -v '(outdated mandoc.db|referenced manual not found)'

clean clobber:

$(MAN1DIR) $(MAN7DIR) $(MAN8DIR):
	$(MKDIR) $@

$(MAN1DIR)/% $(MAN7DIR)/% $(MAN8DIR)/%: %
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<
