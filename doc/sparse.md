
# Sparse branded-zone support with IPS

## Introduction

A sparse branded zone is a non-global zone which shares file-systems
with the global zone and has fewer default packages and services than a
normal `(l)ipkg` branded zone.

This makes them much smaller than a normal non-global zone and faster to
instantiate, however they can still be used as a general-purpose OS instance
(subject to some constraints). They are also ideal for running a VM
instance to provide further isolation and resource controls, similar to
Joyent's KVM zones.

## Shared file-systems

A sparse zone mounts the following file-systems from the global zone via
read-only loopback mounts:

* /usr
* /sbin
* /lib<sup>[1](#nblib)</sup>

<a name="nblib">1</a>: See following section regarding `/lib/svc`

These are mounted using the following lines in the brand `platform.xml`:

```
        <global_mount special="/sbin" directory="/sbin" opt="ro,nodevices" type="lofs" />
        <global_mount special="/usr" directory="/usr" opt="ro,nodevices" type="lofs" />
        <global_mount special="/lib" directory="/lib" opt="ro,nodevices" type="lofs" />
```

### Special consideration for /lib/svc & /usr/lib/fm

Although the `/lib` file-system is being shared with the global zone, the
`/lib/svc` path generally contains different content in a non-global zone
and so cannot be shared. For example some packages only deliver services to a
particular zone type or may deliver a different version of the service.
Similarly for the `/usr/lib/fm` location where some FMA plugins should not
be present in non-global zones.

In a sparse zone, `/lib/svc` and `/usr/lib/fm` are separate ZFS datasets
mounted on top of the relevant directory from the global zone. Since OmniOS
ZFS does not yet support the `overlay` filesystem property, these mounts are
done directly by the brand control scripts in a similar way to that in which
boot-environments are mounted, with the addition of the `-O` flag to permit
overlaying.

The file-systems are left mounted when the zone is halted to facilitate
patching in that state.

In order to protect against problems that may result from attempting to
patch a sparse IPS image when an overlay file-system is not mounted,
a new IPS image property has been added. The `key-files` property contains
a list of files which must be present within the image for it to be considered
complete. If the image is not complete then patching will not be permitted.

During zone creation, a `.org.opensolaris,pkgkey` is created in each overlay
 and set to be an immutable file, and the path is added to the zone image
`key-files` property.

## Partial delivery of packages

Since a sparse zone contains a number of read-only file-systems containing
content from installed packages in the global-zone, installation of packages
which deliver actions to these file-systems will fail. Even in the case
that the new content matches what is already present at the destination
things such as mediated symlinks cause attempted writes to the
target file-system.

The solution for sparse zones it via a new IPS image property called
`exclude-patterns`. This is a list of python regular expressions
anchored at the start but not the end (as is common throughout IPS).

Package installation planning proceeds just as before but during the
consolidation phase, actions that deliver to or remove from a target
matching one of these patterns are suppressed. This suppression mechanism
is the same one that IPS already uses to suppress null changes such as when
a target already matches or a hardlink has been reversed.

For a sparse zone, the `exclude-patterns` property is set to:

```
    [
	'usr/(?!lib/fm)',
	'sbin/',
	'lib/(?!svc)'
    ]
```

With the negative look-aheads allowing (for example) `/lib/svc` whilst
preventing anything else being delivered to the read-only `/lib`.
Note the trailing slash which still allows for delivery of the top-level
directory (and therefore the eventual mountpoint) itself.

## Reducing the number of installed packages

In order to facilitate the reduction of installed packages within a sparse
zone, a new global image variant has been introduced. This variant is
named `opensolaris.imagetype` and defaults to `full` for all images.

Sparse zones have this variant set to `partial` and this allows for selective
inclusion of lines from the `entire` manifest. In particular, packages from
the `drivers/*` and `locale/*` namespaces are excluded from sparse zones.

Since the only non-global zone content delivered by these files is installed
in `/usr`, the driver man pages and locale files are still available to the
sparse zone but the package catalogue is greatly reduced.

## Reducing active services

The default set of running services within a sparse zone is reduced through
the application of a platform-specific service manifest file -
`/etc/svc/profile/platform_sparse.xml`

## Attach/Detach

When a sparse branded zone is detached, the overlay filesystems are modified
to adjust their mount points and restore ZFS auto-mounting so that the root
tree has them in the correct place.

When re-attaching, the filesystem are changed back to use legacy mounting.

## Examples

```
root@sparse:~# pkg variant
VARIANT                                                                VALUE
arch                                                                   i386
opensolaris.imagetype                                                  partial
opensolaris.zone                                                       nonglobal
```

```
root@sparse:~# pkg property
PROPERTY                       VALUE
be-policy                      default
ca-path                        /etc/ssl/certs
check-certificate-revocation   False
content-update-policy          default
dehydrated                     []
exclude-patterns               ['usr/(?!lib/fm)', 'sbin/', 'lib/(?!svc)']
flush-content-cache-on-success True
key-files                      ['lib/svc/.org.opensolaris,pkgkey', 'usr/lib/fm/.org.opensolaris,pkgkey']
mirror-discovery               False
preferred-authority
publisher-search-order         ['omnios', 'extra.omnios']
send-uuid                      True
signature-policy               verify
signature-required-names       []
trust-anchor-directory         etc/ssl/pkg
use-system-repo                False
```

```
root@sparse:~# zfs list -o space
NAME                            AVAIL   USED  USEDSNAP  USEDDS  USEDREFRESERV  USEDCHILD
rpool/zone/sparse                257G  22.6M         0     23K              0      22.6M
rpool/zone/sparse/ROOT           257G  22.6M         0     23K              0      22.6M
rpool/zone/sparse/ROOT/zbe       257G  22.6M         0   21.2M              0      1.34M
rpool/zone/sparse/ROOT/zbe/svc   257G  1.34M         0   1.34M              0          0
```

