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
# Copyright 2024 OmniOS Community Edition (OmniOSce) Association.
#

include ../Makefile.com

all := TARGET = all
clean := TARGET = clean
clobber := TARGET = clobber
install := TARGET = install

SUBDIRS = misc publish/transforms mkcert
PKGLIBFILES = opensolaris.org.sections pkglintrc

ROOTPKGLIB = $(ROOTUSRSHARE)/lib/pkg

FILES= $(PKGLIBFILES:%=$(ROOTPKGLIB)/%)

all clean clobber: $(SUBDIRS)

install: $(SUBDIRS) $(FILES)

$(SUBDIRS): FRC
	@cd $@; pwd; $(MAKE) $(TARGET)

check:

$(ROOTPKGLIB)/%: %
	$(MKDIR) $(@D)
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<

FRC:

