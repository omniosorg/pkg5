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
# Copyright 2023 OmniOS Community Edition (OmniOSce) Association.
#

EXTENSIONS= arch pspawn sha512_t sysattr syscallat
SOURCES= $(EXTENSIONS:%=_%.c)
OUTDIR= cffi_src

all: $(SOURCES)

$(OUTDIR): FRC
	mkdir $@

_%.c: $(OUTDIR) build_%.py
	./$<
	mv $(OUTDIR)/$@ .

clean:
	rm -f $(SOURCES)

clobber: clean
	rm -rf $(OUTDIR)

FRC:

