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
# Copyright (c) 2007, 2019, Oracle and/or its affiliates. All rights reserved.
# Copyright 2016, OmniTI Computer Consulting, Inc. All rights reserved.
# Copyright 2022 OmniOS Community Edition (OmniOSce) Association.
#

include Makefile.com

JOBS = 8

PYTESTS = $(PYVERSIONS:%=runtest/%)
PYMODULES = $(PYVERSIONS:%=modules/%)
TESTRESULTS = $(PYVERSIONS:%=testresults/%)

REQUIREMENTS = requirements.txt

all := TARGET = all
build := TARGET = build
install := TARGET = install
clean := TARGET = clean
clobber := TARGET = clobber
packages := TARGET = install
test := TARGET = test

SUBDIRS=brand util/mkcert

all: build $(SUBDIRS)

build: $(PYVERSIONS)

install: $(PYVERSIONS) .WAIT $(PYMODULES) .WAIT $(SUBDIRS)

clean: $(SUBDIRS) $(PYVERSIONS)
	@cd pkg; pwd; make clean

clobber: $(SUBDIRS) $(PYVERSONS)
	@cd pkg; pwd; make clobber

packages: install
	@cd pkg; pwd; $(MAKE) $(TARGET) check \
	    PATH=$(ROOT)/usr/bin:$$PATH \
	    CC=$(CC)

test: install .WAIT $(PYTESTS) $(TESTRESULTS)

fmt: FRC
	python$(PYVER) -mpip install --quiet --user --upgrade black
	$(HOME)/.local/bin/black --config $(CODE_WS)/.black $(CODE_WS)/src

$(SUBDIRS) cffi_src: FRC
	@cd $@; pwd; $(MAKE) $(TARGET) CC=$(CC)

$(PYVERSIONS): FRC
	python$@ setup.py $(TARGET)

$(PYMODULES): FRC
	../tools/installmodules $(CODE_WS) $(ROOT) $(@F) \
	    $(TARGET) $(ROOTPKGLIB) core $(REQUIREMENTS)

$(PYTESTS): $(PYMODULES)
	-pfexec python$(@F) tests/run.py -t -j $(JOBS)
	-pfexec cp tests/failures.3 tests/failures.$(@F)
	-pfexec cp tests/timing_info.txt tests/timing_info.$(@F)

$(TESTRESULTS): $(PYTESTS)
	@echo
	@echo ">>> Test results for Python $(@F)"
	@cat tests/failures.$(@F)

FRC:

.NO_PARALLEL: $(PYVERSIONS) $(PYTESTS)

