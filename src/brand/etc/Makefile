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
# Copyright 2023 OmniOS Community Edition (OmniOSce) Association.
#

FILES = \
	OMNIbhyve.xml \
	OMNIemu.xml \
	OMNIillumos.xml \
	OMNIkvm.xml \
	OMNIlx.xml OMNIlx26.xml \
	OMNIipkg.xml \
	OMNIlipkg.xml \
	OMNIpkgsrc.xml \
	OMNIsparse.xml \
	SUNWipkg.xml

BRANDDIR = $(ROOTETCZONES)
BRANDFILES = $(FILES:%=$(ROOTETCZONES)/%)

include ../Makefile.com

$(ROOTETCZONES)/OMNIipkg.xml:
	rm -f $@; ln -s SUNWipkg.xml $@

