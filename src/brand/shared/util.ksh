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

[ -n "$_ZONE_LIB_UTIL" ] && return
_ZONE_LIB_UTIL=1

. /usr/lib/brand/shared/log.ksh

function zonepath_to_ds {
	typeset path="${1:?zonepath}"

	df -k "$path" 2>/dev/null | nawk -v zp="$path" '$6 == zp { print $1 }'
}

function zonepath_to_ds_chk {
	typeset path="${1:?zonepath}"
	typeset zoneds=`zonepath_to_ds "$path"`

	if [ -z "$zoneds" ]; then
		# The zone has no dataset and this should have been created
		# automatically by zoneadm. Check for the parent dataset so we
		# can provide a more helpful error message.
		typeset ppath=`dirname "$path"`
		if [ -z "`zonepath_to_ds $ppath`" ]; then
			fatal "Could not find parent dataset for %s\nMake sure that %s is a ZFS dataset" \
			    "$path" "$ppath"
		else
			fatal "Could not find a ZFS dataset for %s" "$path"
		fi
	fi
	echo "$zoneds"
}

#
# Retrieve an attribute from the zone configuration
#
function zone_attr {
	typeset zone="${1:?zone}"
	typeset attr="${2:?attr}"

	zonecfg -z "$zone" info attr name=$attr \
	    | nawk '$1 == "value:" {print $2}'
}

