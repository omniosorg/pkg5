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
# Copyright 2020 OmniOS Community Edition (OmniOSce) Association.
#

BRAND = ipkg
FILES = attach clone common.ksh detach fmri_compare image_install p2v \
	pkgcreatezone pkgrm.lst poststate prestate smf_disable.lst \
	support system-unconfigure uninstall
EFILES = pkgrm.conf smf_disable.conf
BINS = support fmri_compare

BRANDDIR = $(ROOTBRAND)/$(BRAND)
ETCDIR = $(ROOTETCBRAND)/$(BRAND)
BRANDFILES = $(FILES:%=$(BRANDDIR)/%) $(EFILES:%=$(ETCDIR)/%)

include ../Makefile.common

all: $(BINS)

support := LDLIBS = -L/lib -lzonecfg

support: support.c
	$(LINK.c) -o $@ $@.c $(LDLIBS)

fmri_compare: fmri_compare.py
	cp $@.py $@
	chmod 755 $@

$(ETCDIR):
	mkdir -p $(@)

$(ETCDIR)/%: $(ETCDIR) etc/%
	rm -f $@; $(INSTALL) -f $(ETCDIR) -m 0444 $<

