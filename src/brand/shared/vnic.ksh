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

[ -n "$_ZONE_LIB_VNIC" ] && return
_ZONE_LIB_VNIC=1

. /usr/lib/brand/shared/log.ksh

#
# Retrieve a list of on-demand VNICs configured on the zone along with their
# configuration parameters.
#
function demand_vnics {
	zonecfg -z "$ZONENAME" info net | nawk '
		function outp()	{
					if (!shared && global != "-")
						printf("%s %s %s %s %s\n",
						    phys, global, mac, vlan,
						    addr)
				}
		/^net:/		{
					outp()
					phys = global = mac = vlan = addr = "-"
					shared = 0
				}
		/global-nic:/		{ global = $2 }
		/physical:/		{ phys = $2 }
		/mac-addr:/		{ mac = $2 }
		/vlan-id:/		{ vlan = $2 }
		/allowed-address:/	{ addr = $2 }
		# If an address attribute is specified, this is a shared IP
		# zone.
		/\taddress:/	{ shared = 1 }
		END		{ outp() }
	'
}

#
# Configure on-demand VNICs for the zone
#
function config_vnics {
	demand_vnics | while read nic global mac vlan addr; do
		[ -n "$global" -a "$global" != "-" ] || continue
		if [ "$global" = "auto" ]; then
			if [ "$addr" = "-" ]; then
				fail_fatal "%s %s" \
				    "Cannot use 'auto' global NIC" \
				    "without allowed-address."
			fi
			global="`route -n get "$addr" | nawk '
			    / interface:/ {print $2; exit}'`"
			if [ -z "$global" ]; then
				fail_fatal \
				    "Could not determine global-nic for %s" \
				    "$nic"
			fi
		fi
		if dladm show-vnic -p -o LINK $nic >/dev/null 2>&1; then
			# VNIC already exists
			continue
		fi
		log "Creating VNIC $nic/$global (mac: $mac, vlan: $vlan)"

		opt=
		[ "$mac" != "-" ] && opt+=" -m $mac"
		[ "$vlan" != "-" -a "$vlan" != "0" ] && opt+=" -v $vlan"
		if ! dladm create-vnic -l $global $opt $nic; then
			fail_fatal "Could not create VNIC %s/%s" \
			    "$nic" "$global"
		fi

		if [ "$mac" = "-" ]; then
			# Record the assigned MAC address in the zone config
			mac=`dladm show-vnic -p -o MACADDRESS $nic`
			[ -n "$mac" ] && zonecfg -z $ZONENAME \
			    "select net physical=$nic; " \
			    "set mac-addr=$mac; " \
			    "end; exit"
		fi
	done
}

#
# Unconfigure on-demand VNICs for the zone
#
function unconfig_vnics {
	demand_vnics | while read nic global mac vlan addr; do
		[ -n "$global" -a "$global" != "-" ] || continue
		log "Removing VNIC $nic/$global"
		dladm delete-vnic $nic || log "Could not delete VNIC $nic"
	done
}

