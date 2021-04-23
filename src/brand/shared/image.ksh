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
# Copyright 2016 Joyent, Inc.  All rights reserved.
# Copyright 2016 OmniTI Computer Consulting, Inc.  All rights reserved.
# Copyright 2021 OmniOS Community Edition (OmniOSce) Association.

[ -n "$_ZONE_LIB_IMAGE" ] && return
_ZONE_LIB_IMAGE=1

. /usr/lib/brand/shared/util.ksh
. /usr/lib/brand/shared/log.ksh
. /usr/lib/brand/shared/vars.ksh

function seed_zone {
	typeset -i mkroot=1
	typeset bename=
	while getopts "R(norootds)B:(withbe)" opt; do
		case $opt in
		    R)	mkroot=0 ;;
		    B)	bename="$OPTARG" ;;
		esac
	done
	shift OPTIND-1

	typeset zone="${1:?zone}"
	typeset zonepath="${2:?zonepath}"
	typeset seedsrc="${3:?seedsrc}"

	# determine zoneds
	zoneds=`zonepath_to_ds_chk "$zonepath"`

	typeset zroot="$zonepath/root"
	typeset zrootds="$zoneds"
	if [ -n "$bename" ]; then
		zrootds+=/ROOT
		zfs create -o mountpoint=legacy -o zoned=on $zrootds \
		    || fatal "Could not create $zrootds"
		zrootds+=/$bename
		mkroot=1
	elif ((mkroot == 1)); then
		zrootds+="/root"
	fi

	typeset p

	# $seedsrc will be a path to a file, ZFS dataset or ZFS snapshot
	# Determine which it is.
	if [ ! -f "$seedsrc" ]; then
		[[ "$seedsrc" == /* ]] && \
		    fatal "Source file '%s' not found" "$seedsrc"
		seedtype=zfs
		if [[ "$seedsrc" == *@* ]]; then
			p=`zfs list -Ht snapshot -o name "$seedsrc" 2>/dev/null`
			[ "$p" = "$seedsrc" ] || \
			    fatal "Could not find ZFS snapshot %s" "$seedsrc"
		else
			p=`zfs list -Ht filesystem -o name \
			    "$seedsrc" 2>/dev/null`
			[ "$p" = "$seedsrc" ] || \
			    fatal "Could not find ZFS filesystem %s" "$seedsrc"

			# We have a zfs filesystem name.
			# Snapshot it using today's date/time
			typeset snapname=`date -u "+%Y-%m-%d:%H:%M:%S"`
			seedsrc+="@$snapname"
			if ! zfs snapshot "$seedsrc"; then
				fatal "ZFS snapshot (%s) failed (%d)" \
				    "$seedsrc" "$?"
			fi
			log "Snapshotted %s" "$seedsrc"
		fi
	else
		# If the input file is compressed, extract the first portion
		# to determine its type.
		typeset type=`file -b "$seedsrc"`
		typeset filter
		case "$type" in
		    ZFS*)	filter='cat' ;;
		    xz*)	filter='xz -dc' ;;
		    gzip*)	filter='gzip -dc' ;;
		    bzip2*)	filter='bzip2 -dc'
				whence -fp pbzip2 >/dev/null && \
				    filter='pbzip2 -dc'
				;;
		    Zstandard*)	filter='zstd -dc'
				whence -fp zstd >/dev/null || \
				    fatal "Cannot uncompress %s - %s" \
				    "$type" \
				    "no 'zstd' command found on system."
				;;
		    *\ tar\ *)	filter='cat' ;;
		    *)
			fatal "Seed file %s not in a supported format (%s)" \
			    "$seedsrc" "$type"
			;;
		esac

		log "Base file type is '%s'" "$type"
		log "Selected filter '%s'" "$filter"

		typeset tf=`mktemp`
		$filter "$seedsrc" | dd of="$tf" bs=1024 count=1024
		typeset type=`file -b "$tf"`
		rm -f "$tf"

		log "Underlying file type is '%s'" "$type"

		case "$type" in
		    ZFS*)	seedtype=zfsrecv ;;
		    *\ tar\ *)	seedtype=tar ;;
		    *)
			fatal "Seed file %s not in a supported format (%s)" \
			    "$seedsrc" "$type"
			;;
		esac
	fi

	case "$seedtype" in
	    zfs)
		elog "Installing zone from ZFS filesystem %s" "$seedsrc"

		elog "Cloning from snapshot %s" "$seedsrc"
		# If the seed replaces the entire dataset then
		# first remove it (it will contain at least logs)
		if [ "$mkroot" -eq 0 ] && ! zfs destroy -fr "$zrootds"; then
			fatal "ZFS destroy %s failed (%d)" "$zrootds" "$?"
		fi
		if ! zfs clone "$seedsrc" "$zrootds"; then
			fatal "ZFS clone (%s to %s) failed (%d)" \
			    "$seedsrc" "$zrootds" "$?"
		fi
		;;

	    zfsrecv)
		elog "Installing zone from ZFS stream" "$seedsrc"
		# If the seed replaces the entire dataset then
		# first remove it (it will contain at least logs)
		if [ "$mkroot" -eq 0 ] && ! zfs destroy -fr "$zrootds"; then
			fatal "ZFS destroy %s failed (%d)" "$zrootds" "$?"
		fi
		$filter "$seedsrc" | zfs recv -F "$zrootds"
		[ $? -ne 0 ] && fatal "ZFS receive command failed (%s)" "$?"
		;;

	    tar)
		elog "Installing zone from tar file %s" "$seedsrc"
		set -e
		if ((mkroot == 1)); then
			zfs create "$zrootds" \
			    || fatal "Cannot create $zrootds"
		fi
		[ -d $zroot ] || mkdir $zroot
		if [ -n "$bename" ]; then
			mount -F zfs $zrootds $zroot \
			    || fatal "Could not mount $zrootds on $zroot"
		fi
		chmod 0755 $zroot
		chgrp sys $zroot
		$filter "$seedsrc" | gtar -C $zroot -xf -
		if [ -n "$bename" ]; then
			umount $zroot
		fi
		;;
	esac

	[ -d $zroot ] || mkdir $zroot

	if [ -n "$bename" ]; then
		typeset gzuuid=`get_gzbe_uuid`
		[ -n "$gzuuid" ] || fatal "Could not get GZ BE UUID"
		zfs set $PROP_ACTIVE=on $zrootds
		zfs set $PROP_PARENT=$gzuuid $zrootds
		zfs set canmount=noauto $zrootds
	elif [ ! -d "$zroot/dev" ]; then
		mkdir -m 0755 "$zroot/dev"
		chgrp sys "$zroot/dev"
	fi
}

