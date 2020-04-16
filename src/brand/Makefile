#
# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
# or http://www.opensolaris.org/os/licensing.
# See the License for the specific language governing permissions
# and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at usr/src/OPENSOLARIS.LICENSE.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END
#

#
# Copyright (c) 2007, 2010, Oracle and/or its affiliates. All rights reserved.
# Copyright (c) 2012, OmniTI Computer Consulting, Inc. All rights reserved.
# Copyright 2020 OmniOS Community Edition (OmniOSce) Association.
#

MACH:sh = uname -p

INSTALL = /usr/sbin/install

ROOT = ../../proto/root_${MACH}
ROOTETC = $(ROOT)/etc
ROOTETCZONES = $(ROOT)/etc/zones
ROOTETCBRAND = $(ROOT)/etc/brand/ipkg
ROOTUSRLIB = $(ROOT)/usr/lib
ROOTBRAND = $(ROOTUSRLIB)/brand
SHAREDBRANDPKG = $(ROOTBRAND)/shared
ROOTBRANDPKG = $(ROOTBRAND)/ipkg
LINKBRANDPKG = $(ROOTBRAND)/lipkg
SPARSEBRANDPKG = $(ROOTBRAND)/sparse
PKGSRCBRANDPKG = $(ROOTBRAND)/pkgsrc
BHYVEBRANDPKG = $(ROOTBRAND)/bhyve
KVMBRANDPKG = $(ROOTBRAND)/kvm
ILLUMOSBRANDPKG = $(ROOTBRAND)/illumos
LXBRANDPKG = $(ROOTBRAND)/lx

ROOTDIRS = \
	$(ROOT) \
	$(ROOTETC) \
	$(ROOTETCBRAND) \
	$(ROOTETCZONES) \
	$(ROOTUSRLIB) \
	$(ROOTBRAND) \
	$(SHAREDBRANDPKG) \
	$(ROOTBRANDPKG) \
	$(LINKBRANDPKG) \
	$(SPARSEBRANDPKG) \
	$(PKGSRCBRANDPKG) \
	$(BHYVEBRANDPKG) \
	$(KVMBRANDPKG) \
	$(ILLUMOSBRANDPKG) \
	$(LXBRANDPKG)

ROOTFILES = \
	$(ROOTETCBRAND)/pkgrm.conf \
	$(ROOTETCBRAND)/smf_disable.conf \
	$(ROOTETCZONES)/OMNIlipkg.xml \
	$(ROOTETCZONES)/SUNWipkg.xml \
	$(ROOTETCZONES)/OMNIipkg.xml \
	\
	$(SHAREDBRANDPKG)/firewall.ksh \
	$(SHAREDBRANDPKG)/state.ksh \
	$(SHAREDBRANDPKG)/vnic.ksh \
	\
	$(ROOTBRANDPKG)/attach \
	$(ROOTBRANDPKG)/clone \
	$(ROOTBRANDPKG)/common.ksh \
	$(ROOTBRANDPKG)/detach \
	$(ROOTBRANDPKG)/fmri_compare \
	$(ROOTBRANDPKG)/image_install \
	$(ROOTBRANDPKG)/p2v \
	$(ROOTBRANDPKG)/pkgcreatezone \
	$(ROOTBRANDPKG)/pkgrm.lst \
	$(ROOTBRANDPKG)/poststate \
	$(ROOTBRANDPKG)/prestate \
	$(ROOTBRANDPKG)/smf_disable.lst \
	$(ROOTBRANDPKG)/support \
	$(ROOTBRANDPKG)/system-unconfigure \
	$(ROOTBRANDPKG)/uninstall \
	\
	$(ROOTETCZONES)/OMNIsparse.xml \
	$(SPARSEBRANDPKG)/attach \
	$(SPARSEBRANDPKG)/clone \
	$(SPARSEBRANDPKG)/common.ksh \
	$(SPARSEBRANDPKG)/config.xml \
	$(SPARSEBRANDPKG)/detach \
	$(SPARSEBRANDPKG)/pkgcreatezone \
	$(SPARSEBRANDPKG)/platform.xml \
	$(SPARSEBRANDPKG)/poststate \
	$(SPARSEBRANDPKG)/prestate \
	$(SPARSEBRANDPKG)/profile.xml \
	$(SPARSEBRANDPKG)/uninstall \
	$(SPARSEBRANDPKG)/verifyadm \
	\
	$(ROOTETCZONES)/OMNIpkgsrc.xml \
	$(PKGSRCBRANDPKG)/config.xml \
	$(PKGSRCBRANDPKG)/platform.xml \
	$(PKGSRCBRANDPKG)/postinstall \
	$(PKGSRCBRANDPKG)/profile.xml \
	\
	$(ROOTETCZONES)/OMNIbhyve.xml \
	$(BHYVEBRANDPKG)/config.xml \
	$(BHYVEBRANDPKG)/platform.xml \
	$(BHYVEBRANDPKG)/init \
	$(BHYVEBRANDPKG)/createzone \
	$(BHYVEBRANDPKG)/socat \
	$(BHYVEBRANDPKG)/support \
	\
	$(ROOTETCZONES)/OMNIkvm.xml \
	$(KVMBRANDPKG)/config.xml \
	$(KVMBRANDPKG)/platform.xml \
	$(KVMBRANDPKG)/init \
	$(KVMBRANDPKG)/support \
	\
	$(ROOTETCZONES)/OMNIillumos.xml \
	$(ILLUMOSBRANDPKG)/config.xml \
	$(ILLUMOSBRANDPKG)/createzone \
	$(ILLUMOSBRANDPKG)/platform.xml \
	$(ILLUMOSBRANDPKG)/poststate \
	$(ILLUMOSBRANDPKG)/prestate \
	$(ILLUMOSBRANDPKG)/uninstall \
	\
	$(LXBRANDPKG)/prestate \
	$(LXBRANDPKG)/poststate

BIN = \
	support \
	fmri_compare

support := LDLIBS = -L/lib -lzonecfg

all := TARGET = all

support: support.c
	$(LINK.c) -o $@ $@.c $(LDLIBS)

fmri_compare: fmri_compare.py
	cp $@.py $@
	chmod 755 $@

all: fmri_compare

clean:
	rm -f $(BIN)

clobber: clean
	rm -f $(ROOTFILES)
	rm -fr $(ROOTBRAND)

install: $(ROOTFILES)

$(ROOT) $(ROOTETC) $(ROOTETCBRAND) $(ROOTETCZONES) $(ROOTUSRLIB) \
	$(ROOTBRAND) $(SHAREDBRANDPKG) $(ROOTBRANDPKG) $(LINKBRANDPKG) \
	$(ILLUMOSBRANDPKG) $(SPARSEBRANDPKG) $(PKGSRCBRANDPKG) \
	$(BHYVEBRANDPKG) $(KVMBRANDPKG) $(LXBRANDPKG):
	mkdir -p $@

$(ROOTETCBRAND)/%: $(ROOTETCBRAND) %
	rm -f $@; $(INSTALL) -f $(ROOTETCBRAND) -m 0644 $<

$(ROOTETCZONES)/%: $(ROOTETCZONES) %
	rm -f $@; $(INSTALL) -f $(ROOTETCZONES) -m 0444 $<

$(ROOTETCZONES)/OMNIipkg.xml: $(ROOTETCZONES)
	rm -f $@; ln -s SUNWipkg.xml $@

$(ROOTBRANDPKG)/%: $(ROOTBRANDPKG) %
	rm -f $@; $(INSTALL) -f $(ROOTBRANDPKG) -m 0444 $<

$(SHAREDBRANDPKG)/%: $(SHAREDBRANDPKG) shared/%
	rm -f $@; $(INSTALL) -f $(SHAREDBRANDPKG) -m 0444 $<

$(SPARSEBRANDPKG)/%: $(SPARSEBRANDPKG) sparse/%
	rm -f $@; $(INSTALL) -f $(SPARSEBRANDPKG) -m 0444 $<

$(PKGSRCBRANDPKG)/profile.xml: $(PKGSRCBRANDPKG)
	rm -f $@; ln -s ../sparse/profile.xml $@

$(PKGSRCBRANDPKG)/%: $(PKGSRCBRANDPKG) pkgsrc/%
	rm -f $@; $(INSTALL) -f $(PKGSRCBRANDPKG) -m 0444 $<

$(BHYVEBRANDPKG)/%: $(BHYVEBRANDPKG) bhyve/%
	rm -f $@; $(INSTALL) -f $(BHYVEBRANDPKG) -m 0444 $<

$(KVMBRANDPKG)/%: $(KVMBRANDPKG) kvm/%
	rm -f $@; $(INSTALL) -f $(KVMBRANDPKG) -m 0444 $<

$(ILLUMOSBRANDPKG)/%: $(ILLUMOSBRANDPKG) illumos/%
	rm -f $@; $(INSTALL) -f $(ILLUMOSBRANDPKG) -m 0444 $<

$(LXBRANDPKG)/%: $(LXBRANDPKG) lx/%
	rm -f $@; $(INSTALL) -f $(LXBRANDPKG) -m 0444 $<

