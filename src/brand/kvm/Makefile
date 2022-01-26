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
# Copyright 2021 OmniOS Community Edition (OmniOSce) Association.
#

BRAND = kvm
FILES = config.xml init platform.xml support uninstall
BINS = init
BHYVELINKS = createzone socat

BRANDDIR = $(ROOTBRAND)/$(BRAND)
ROOTBHYVELINKS = $(BHYVELINKS:%=$(BRANDDIR)/%)
BRANDFILES = $(FILES:%=$(BRANDDIR)/%) $(ROOTBHYVELINKS)

all: $(BINS)

include ../Makefile.common

$(ROOTBHYVELINKS): FRC
	rm -f $@; ln -s ../bhyve/$(@F) $@

FRC:

