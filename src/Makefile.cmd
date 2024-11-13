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

include Makefile.com

BINS = \
	pkg \
	pkgdepend \
	pkgrepo \
	pkgdiff \
	pkgfmt \
	pkglint \
	pkgmerge \
	pkgmogrify \
	pkgsurf \
	pkgsend \
	pkgrecv \
	pkgsign

LIBS = \
	pkg.depotd

TARGETS= $(BINS:%=$(ROOTUSRBIN)/%) $(LIBS:%=$(ROOTUSRLIB)/%)

$(ROOTUSRBIN)/pkg		:= SRC = client.py
$(ROOTUSRBIN)/pkgdepend		:= SRC = pkgdep.py
$(ROOTUSRBIN)/pkgrepo		:= SRC = pkgrepo.py
$(ROOTUSRBIN)/pkgdiff		:= SRC = util/publish/pkgdiff.py
$(ROOTUSRBIN)/pkgfmt		:= SRC = util/publish/pkgfmt.py
$(ROOTUSRBIN)/pkglint		:= SRC = util/publish/pkglint.py
$(ROOTUSRBIN)/pkgmerge		:= SRC = util/publish/pkgmerge.py
$(ROOTUSRBIN)/pkgmogrify	:= SRC = util/publish/pkgmogrify.py
$(ROOTUSRBIN)/pkgsurf		:= SRC = util/publish/pkgsurf.py
$(ROOTUSRBIN)/pkgsend		:= SRC = publish.py
$(ROOTUSRBIN)/pkgrecv		:= SRC = pull.py
$(ROOTUSRBIN)/pkgsign		:= SRC = sign.py
$(ROOTUSRLIB)/pkg.depotd	:= SRC = depot.py

all clean clobber:

install: $(TARGETS)
	python$(PYVER) $(PYCOMPILE_OPTS) $(TARGETS)

$(TARGETS): FRC
	$(MKDIR) $(@D)
	$(RM) $@
	$(SED) '1s/python3 /python$(PYVER) /' < $(SRC) > $@
	$(CHMOD) 555 $@

# shebang...

FRC:

