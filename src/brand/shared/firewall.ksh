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

[ -n "$_ZONE_LIB_FIREWALL" ] && return
_ZONE_LIB_FIREWALL=1

. /usr/lib/brand/shared/log.ksh

# Set up GZ-managed firewall rules for the zone
function setup_firewall {
	if [ -f $ZONEPATH/etc/ipf.conf ]; then
		ipf_conf=$ZONEPATH/etc/ipf.conf
		ipf6_conf=$ZONEPATH/etc/ipf6.conf
		ipnat_conf=$ZONEPATH/etc/ipnat.conf
	elif [ -f $ZONEPATH/config/ipf.conf ]; then
		# Joyent SmartOS uses config/
		ipf_conf=$ZONEPATH/config/ipf.conf
		ipf6_conf=$ZONEPATH/config/ipf6.conf
		ipnat_conf=$ZONEPATH/config/ipnat.conf
	else
		return
	fi

	log "Enabling zone firewall ($ipf_conf)"
	ipf -GE $ZONENAME || fail_fatal "error enabling ipf"

	# Flush
	ipf -GFa $ZONENAME || fail_fatal "error flushing ipf (IPv4)"
	ipf -6GFa $ZONENAME || fail_fatal "error flushing ipf (IPv6)"
	ipnat -CF -G $ZONENAME >/dev/null 2>&1 || \
	    fail_fatal "error flushing ipnat"

	# IPv4
	ipf -Gf $ipf_conf $ZONENAME || \
	    fail_fatal "error loading ipf config for IPv4"

	# IPv6
	[ -f $ipf6_conf ] && ! ipf -6Gf $ipf6_conf $ZONENAME && \
	    fail_fatal "error loading ipf config for IPv6"

	# NAT
	[ -f $ipnat_conf ] && ! ipnat -G $ZONENAME -f $ipnat_conf && \
	    fail_fatal "error loading ipnat config"

	ipf -Gy $ZONENAME >/dev/null 2>&1 || \
	    fail_fatal "error syncing ipf interfaces"
}

