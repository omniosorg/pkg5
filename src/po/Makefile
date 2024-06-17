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
# Copyright (c) 2024, Oracle and/or its affiliates.
# Copyright 2024 OmniOS Community Edition (OmniOSce) Association.
#

include ../Makefile.com

XGETTEXT =	/usr/gnu/bin/xgettext
MSGFMT =	/usr/bin/msgfmt

LOCALES = ar ca cs de es fr he hu id it ja ko nl pl \
	pt_BR ru sk sv zh_CN zh_HK zh_TW

CATALOGS =	$(LOCALES:=.mo)

ROOTLOCALES =	$(LOCALES:%=$(ROOTUSRSHARELOCALE)/%/LC_MESSAGES/pkg.mo)
ROOTPOT =	$(ROOTUSRSHARELOCALE)/__LOCALE__/LC_MESSAGES/pkg.pot

all: $(CATALOGS) pkg.pot

$(ROOTPOT): pkg.pot
	$(MKDIR) $(@D)
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 pkg.pot

$(ROOTUSRSHARELOCALE)/%/LC_MESSAGES/pkg.mo: %.mo
	$(MKDIR) $(@D)
	$(RM) $@; $(INSTALL) -f $(@D) -m 0444 $<
	$(MV) $(@D)/$(<F) $@

install: all $(ROOTPOT) $(ROOTLOCALES)

%.mo: %.po
	$(MSGFMT) -o $@ $^

pkg.pot: POTFILES.in
	$(XGETTEXT) --add-comments --keyword=N_ \
	    --from-code=UTF-8 -f POTFILES.in -D .. -o pkg.pot

check:
	$(XGETTEXT) --keyword=N_ --from-code=UTF-8 -f POTFILES.in \
	    -D .. -o /dev/null 2>i18n_errs.txt
	@if [ -s i18n_errs.txt ]; then \
		echo "The following i18n errors were detected:"; \
		echo "(this list is saved in i18n_errs.txt)"; \
		cat i18n_errs.txt; \
		exit 1; \
	fi

clean:
	rm -f pkg.pot *.mo i18n_errs.txt

clobber: clean
