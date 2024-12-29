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
# Copyright 2024 OmniOS Community Edition (OmniOSce) Association.
#

include Makefile.com

JOBS = 8

# These are artificial targets used to run actions against all current python
# versions (in PYVERSIONS). In rules the target python version can be picked
# up via $(@F).
PYTESTS = $(PYVERSIONS:%=runtest/%)
EXTMODULES = $(PYVERSIONS:%=extmodules/%)
TESTRESULTS = $(PYVERSIONS:%=testresults/%)

REQUIREMENTS = requirements.txt

TARGET = all
all := TARGET = all
install := TARGET = install
install-cmd := TARGET = install
clean := TARGET = clean
clobber := TARGET = clobber
check := TARGET = check
packages := TARGET = install
test := TARGET = install

SUBDIRS= cffi_src modules brand util man po svc web
brand: cffi_src modules util $(EXTMODULES)

all: $(SUBDIRS)

install: install-cmd $(SUBDIRS) $(EXTMODULES)

clean clobber: $(SUBDIRS) pkg

packages: install
	@cd pkg; pwd; $(MAKE) $(TARGET) check \
	    PATH=$(ROOT)/usr/bin:$$PATH \
	    CC=$(CC)

test: install .WAIT $(PYTESTS) $(TESTRESULTS)

fmt: FRC
	python$(PYVER) -mpip install --quiet --user --upgrade black
	$(HOME)/.local/bin/black --config $(CODE_WS)/.black $(CODE_WS)/src

_bandit: FRC
	python$(PYVER) -mpip install --quiet --user --upgrade bandit

check: _bandit $(SUBDIRS)
	python$(PYVER) -m bandit -r -q -c tests/banditrc . \
		-b tests/bandit-baseline.json

baseline: _bandit
	python$(PYVER) -m bandit -r -q -c tests/banditrc . \
		-o tests/bandit-baseline.json -f json || true

$(SUBDIRS) pkg: FRC
	@cd $@; pwd; $(MAKE) $(TARGET) CC=$(CC)

install-cmd: FRC
	$(MAKE) -f Makefile.cmd $(TARGET)

$(EXTMODULES):
	../tools/installmodules $(CODE_WS) $(ROOT) $(@F) \
	    $(TARGET) $(ROOTPKGLIB) core $(REQUIREMENTS)

$(PYTESTS): $(EXTMODULES)
	-pfexec python$(@F) tests/run.py -t -j $(JOBS)
	-pfexec cp tests/failures.3 tests/failures.$(@F)
	-pfexec cp tests/timing_info.txt tests/timing_info.$(@F)

$(TESTRESULTS): $(PYTESTS)
	@echo
	@echo ">>> Test results for Python $(@F)"
	@cat tests/failures.$(@F)

FRC:

.NO_PARALLEL: $(PYTESTS)

