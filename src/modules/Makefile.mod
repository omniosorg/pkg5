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

SUSEPY.cmd = echo $(USEPY) | tr -d .
SUSEPY = $(SUSEPY.cmd:sh)

VERSION.cmd = git show --format=%h --no-patch
VERSION = $(VERSION.cmd:sh)

ROOTPYPKG= $(ROOT)/usr/lib/python$(USEPY)/vendor-packages/pkg

PYFILES.cmd = find . -type f -name \*.py | cut -c3-
PYFILES = $(PYFILES.cmd:sh)
ROOTPYFILES = $(PYFILES:%=$(ROOTPYPKG)/%)

SKIPFILES = gui/repository.py client/rad_pkg.py
ROOTSKIPFILES = $(SKIPFILES:%=$(ROOTPYPKG)/%)

all clean clobber:

install: $(ROOTPYPKG) $(ROOTPYFILES)
	$(SED) -i '/^VERSION/s/unknown/$(VERSION)/' $(ROOTPYPKG)/__init__.py
	python$(USEPY) $(PYCOMPILE_OPTS) $(ROOTPYPKG)

$(ROOTPYPKG): FRC
	$(MKDIR) $@

$(ROOTPYPKG)/%: %
	$(MKDIR) $(@D)
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<

$(ROOTSKIPFILES): FRC
	$(RM) $@

FRC:

