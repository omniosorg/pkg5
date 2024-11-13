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

include ../../Makefile.com

ROOTETCSECURITY = $(ROOTETC)/security

SECFILES = \
	auth_attr.d/package\:pkg \
	exec_attr.d/package\:pkg
ETCFILES = \
	user_attr.d/package\:pkg

DIRS = \
	usr/share/pkg/ignored_deps \
	var/cache/pkg/mirror \
	var/log/pkg/mirror

ROOTFILES= $(SECFILES:%=$(ROOTETCSECURITY)/%) $(ETCFILES:%=$(ROOTETC)/%)
ROOTDIRS= $(DIRS:%=$(ROOT)/%)

all check clean clobber:

install: $(ROOTFILES) $(ROOTDIRS)

$(ROOTETC)/%: %
	$(MKDIR) $(@D)
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<

$(ROOTETCSECURITY)/%: %
	$(MKDIR) $(@D)
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<

$(ROOT)/%: FRC
	$(MKDIR) $@

FRC:

