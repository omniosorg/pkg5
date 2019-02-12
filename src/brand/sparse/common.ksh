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

overlays="
	svc:lib/svc
	fm:usr/lib/fm
"

find_active_ds()
{
	get_current_gzbe
	get_zonepath_ds $ZONEPATH
	get_active_ds $CURRENT_GZBE $ZONEPATH_DS
}

create_overlays()
{
	typeset rootfs=$1
	for ov in $overlays; do
		ds=${ov%:*}
		zfs create -o canmount=noauto $rootfs/$ds \
		    || fail_fatal "$f_zfs_create"
	done
}

umount_overlays()
{
	[ $sparsedebug -gt 1 ] && echo "umount_overlays()"
	for ov in $overlays; do
		mp=${ov#*:}
		if mount -p | cut -d' ' -f3 | egrep -s "^$ZONEPATH/root/$mp$"
		then
			[ $sparsedebug -gt 1 ] && echo " ... unmounting $mp"
			umount $ZONEPATH/root/$mp
		fi
	done
}

mount_overlays()
{
	[ $sparsedebug -gt 1 ] && echo "mount_overlays()"
	for ov in $overlays; do
		ds=${ov%:*}
		mp=${ov#*:}
		[ -d $ZONEPATH/root/$mp ] || mkdir -p $ZONEPATH/root/$mp
		mount -F zfs -O $ACTIVE_DS/$ds $ZONEPATH/root/$mp \
		    || fail_fatal "$f_zfs_mount"
		keyf=$ZONEPATH/root/$mp/.org.opensolaris,pkgkey
		if [ ! -f $keyf ]; then
			# Create a flag file which is checked for by pkg before
			# performing operations on this zone
			touch $keyf
			/bin/chmod S+vimmutable $keyf
		fi
		if [ -d $ZONEPATH/root/etc ] && \
		    ! egrep -s "^$ACTIVE_DS/$ds " $ZONEPATH/root/etc/vfstab
		    then
			# Add the overlay to the zone vfstab. This ensures that
			# it is properly mounted when mounting alternate
			# BEs, such as when doing new-be pkg update.
			cat <<- EOM >> $ZONEPATH/root/etc/vfstab
				$ACTIVE_DS/$ds - /$mp zfs - no -
			EOM
		fi
	done
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

sanity_check() {
	typeset dir="$1"
	shift
	res=0

	#
	# Check for some required directories.
	#
	checks="etc etc/svc var var/svc"
	for x in $checks; do
		if [[ ! -e $dir/$x ]]; then
			log "$f_sanity_detail" "$x" "$dir"
			res=1
		fi
	done
	if (( $res != 0 )); then
		log "$f_sanity_sparse"
		log "$sanity_fail"
		fatal "$install_fail" "$ZONENAME"
	fi

	vlog "$sanity_ok"
}

# Vim hints
# vim:fdm=marker
