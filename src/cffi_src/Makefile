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

#
# Copyright 2024 OmniOS Community Edition (OmniOSce) Association.
#

include ../Makefile.com

SUBDIRS= $(PYVERSIONS:%=src/%)

all := TARGET = all
install := TARGET = install
clean := TARGET = clean
clobber := TARGET = clobber

all install clean: $(SUBDIRS)

clobber: $(SUBDIRS)
	$(RMDIR) $(SUBDIRS) src

check:

$(SUBDIRS): FRC
	$(MKDIR) $@
	@cd $@; pwd; $(MAKE) -f ../../Makefile.ext $(TARGET) USEPY=$(@F)

FRC:

