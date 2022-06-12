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
# Copyright 2022 OmniOS Community Edition (OmniOSce) Association.
#

BRAND = bhyve
FILES = boot config.xml createzone init platform.xml socat support uninstall \
	bundle.py uefi/__init__.py uefi/align.py uefi/vars.py
BINS = init boot

PYMODULES = $(PYVERSIONS:%=modules/%)

BRANDDIR = $(ROOTBRAND)/$(BRAND)
BRANDFILES = $(FILES:%=$(BRANDDIR)/%) $(PYMODULES)

LDLIBS += -lzfs

all: $(BINS)

init: init.c
	$(LINK.c) -o $@ $@.c $(LDLIBS)
	$(POST_PROCESS)

include ../Makefile.com

$(BRANDDIR)/uefi: FRC
	mkdir -p $@

$(BRANDFILES): $(BRANDDIR)/uefi

$(PYMODULES): FRC
	$(CODE_WS)/tools/installmodules $(CODE_WS) $(ROOT) $(@F) \
            install $(BRANDDIR) bhyve requirements.txt

FRC:
