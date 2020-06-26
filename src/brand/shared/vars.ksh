#!/bin/ksh -p
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

[ -n "$_ZONE_LIB_VARS" ] && return
_ZONE_LIB_VARS=1

ZONE_STATE_CONFIGURED=0	# never see
ZONE_STATE_INCOMPLETE=1	# never see
ZONE_STATE_INSTALLED=2
ZONE_STATE_READY=3
ZONE_STATE_RUNNING=4
ZONE_STATE_SHUTTING_DOWN=5
ZONE_STATE_DOWN=6
ZONE_STATE_MOUNTED=7
ZONE_STATE_SYSBOOT=99

ZONE_STATE_CMD_READY=0
ZONE_STATE_CMD_BOOT=1
ZONE_STATE_CMD_HALT=4

# These values must be kept synchronised with <sys/zone.h>

ZONE_SUBPROC_OK=0
ZONE_SUBPROC_USAGE=253
ZONE_SUBPROC_NOTCOMPLETE=254
ZONE_SUBPROC_FATAL=255

