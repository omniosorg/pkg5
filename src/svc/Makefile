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

SVCCFG = /usr/sbin/svccfg

ROOTLIBSVC = $(ROOT)/lib/svc

ROOTMANIFESTDIR = $(ROOTLIBSVC)/manifest/application/pkg
ROOTMETHODDIR = $(ROOTLIBSVC)/method
ROOTSHAREDIR = $(ROOTLIBSVC)/share

DIRS = $(ROOTMANIFESTDIR) $(ROOTMETHODDIR) $(ROOTSHAREDIR)

MANIFESTS = \
	pkg-mdns.xml \
	pkg-mirror.xml \
	pkg-repositories-setup.xml \
	pkg-server.xml

METHODS = \
	svc-pkg-mdns \
	svc-pkg-mirror \
	svc-pkg-repositories-setup \
	svc-pkg-server

SHARED = \
	pkg5_include.sh

all:

install: $(DIRS) \
	$(MANIFESTS:%=$(ROOTMANIFESTDIR)/%) \
	$(METHODS:%=$(ROOTMETHODDIR)/%) \
	$(SHARED:%=$(ROOTSHAREDIR)/%)

clean clobber:

check: $(MANIFESTS:%=check-%)

check-%: %
	$(SVCCFG) validate $<

$(DIRS):
	$(MKDIR) $@

$(ROOTMANIFESTDIR)/% $(ROOTMETHODDIR)/% $(ROOTSHAREDIR)/%: %
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<

