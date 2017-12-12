# {{{ CDDL HEADER
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
# }}}

# Copyright 2017 OmniOS Community Edition (OmniOSce) Association.

. /usr/lib/brand/ipkg/common.ksh

sparsedebug=0

find_active_ds()
{
	get_current_gzbe
	get_zonepath_ds $ZONEPATH
	get_active_ds $CURRENT_GZBE $ZONEPATH_DS
}

umount_overlays()
{
	# Unmount the overlay lib/svc
	[ $sparsedebug -gt 1 ] && echo "umount_overlays()"
	if mount -p | cut -d' ' -f3 | egrep -s "^$ZONEPATH/root/lib/svc$"; then
		[ $sparsedebug -gt 1 ] && echo " ... unmounting"
		umount $ZONEPATH/root/lib/svc
	fi
}

mount_overlays()
{
	# Mount lib/svc
	[ $sparsedebug -gt 1 ] && echo "mount_overlays()"
	[ -d $ZONEPATH/root/lib/svc ] || mkdir -p $ZONEPATH/root/lib/svc
	mount -F zfs -O $ACTIVE_DS/svc $ZONEPATH/root/lib/svc \
	    || fail_fatal "$f_zfs_mount"
	keyf=$ZONEPATH/root/lib/svc/.org.opensolaris,pkgkey
	if [ ! -f $keyf ]; then
		# Create a flag file which is checked for by pkg before
		# performing operations on this zone
		touch $keyf
		/bin/chmod S+vimmutable $keyf
	fi
}

umount_active_ds()
{
	[ $sparsedebug -gt 1 ] && echo "umount_active_ds()"
	if mount -p | cut -d' ' -f3 | egrep -s "^$ZONEPATH/root$"; then
		[ $sparsedebug -gt 1 ] && echo " ... unmounting"
		umount $ZONEPATH/root || fail_fatal "$f_zfs_unmount"
	fi
}

mount_active_ds() {
	[ $sparsedebug -gt 1 ] && echo "mount_active_ds()"
	mount -F zfs $ACTIVE_DS $ZONEPATH/root || fail_fatal "$f_zfs_mount"
}

# Vim hints
# vim:fdm=marker
