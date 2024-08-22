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

ROOTUSRSHARELIB = $(ROOTUSRSHARE)/lib
ROOTPKGWEB = $(ROOTUSRSHARELIB)/pkg/web

THEMESDIR = $(ROOTPKGWEB)/_themes
THEMES = default omnios.org

DIRS = \
       $(ROOTPKGWEB) \
       $(ROOTPKGWEB)/en \
       $(THEMESDIR) \
       $(THEMES:%=$(THEMESDIR)/%) \
       $(THEMES:%=$(THEMESDIR)/%/en)

THEME_DEFAULT = \
	en/base.css \
	en/body_end.shtml \
	en/footer.shtml \
	en/head_end.shtml \
	en/header.shtml \
	feed-icon-14x14.png

THEME_OOCE = \
	en/base.css \
	en/body_end.shtml \
	en/footer.shtml \
	en/head_end.shtml \
	en/header.shtml \
	feed-icon-14x14.png \
	logo_ban0.png \
	logo_ban0.svg

THEME_SHARED = \
	p5i-link.png \
	pkg-block-icon.png \
	pkg-block-logo.png \
	pkg-block-logo.svg \
	yui-reset-font-grids_base-min.css

FILES = \
	en/advanced_search.shtml \
	en/base.shtml \
	en/catalog.shtml \
	en/index.shtml \
	en/layout.shtml \
	en/search.shtml \
	en/stats.shtml \
	config.shtml \
	index.shtml \
	robots.txt \
	shared.shtml

all:

install: $(DIRS) \
	$(FILES:%=$(ROOTPKGWEB)/%) \
	$(THEME_SHARED:%=$(THEMESDIR)/%) \
	$(THEME_DEFAULT:%=$(THEMESDIR)/default/%) \
	$(THEME_OOCE:%=$(THEMESDIR)/omnios.org/%)

check clean clobber:

$(DIRS):
	$(MKDIR) $@

$(ROOTPKGWEB)/%: %
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<

