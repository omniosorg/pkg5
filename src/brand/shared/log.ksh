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

[ -n "$_ZONE_LIB_LOG" ] && return
_ZONE_LIB_LOG=1

. /usr/lib/brand/shared/vars.ksh

function setuplog {
	[ -n "$LOGFILE" ] && return
	if [ -n "$ZONEPATH" ]; then
		mkdir -p "$ZONEPATH/log"
		LOGFILE="$ZONEPATH/log/zone.log"
	else
		LOGFILE="/tmp/zone.log"
	fi
	touch "$LOGFILE"
	chown root:root "$LOGFILE"
	chmod 600 "$LOGFILE"
	exec 2>>"$LOGFILE"
}

function clearlog {
	setuplog
	: >"$LOGFILE"
	exec 2>>"$LOGFILE"
}

function log {
        typeset fmt="$1"; shift

	setuplog

        [ -n "$OPT_V" ] && printf "${fmt}\n" "$@"
        printf "[`date`] $fmt\n" "$@" >&2
}

function elog {
	OPT_V=1 log "$@"
}

function error {
        typeset fmt="$1"; shift

	OPT_V=1 log "ERROR: $fmt" "$@" >> /dev/stderr
}

function fatal {
	error "$@"
	exit $ZONE_SUBPROC_FATAL
}

