.. CDDL HEADER START

.. The contents of this file are subject to the terms of the
   Common Development and Distribution License (the "License").
   You may not use this file except in compliance with the License.

.. You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
   or http://www.opensolaris.org/os/licensing.
   See the License for the specific language governing permissions
   and limitations under the License.

.. When distributing Covered Code, include this CDDL HEADER in each
   file and include the License file at usr/src/OPENSOLARIS.LICENSE.
   If applicable, add the following below this CDDL HEADER, with the
   fields enclosed by brackets "[]" replaced with your own identifying
   information: Portions Copyright [yyyy] [name of copyright owner]

.. CDDL HEADER END


.. Copyright (c) 2010, Oracle and/or its affiliates. All rights reserved.

Tags and attributes
-------------------

Definitions
~~~~~~~~~~~

    Both packages and actions within a package can carry metadata, which
    we informally refer to as attributes and tags.  Both attributes and
    tags have a name and one or more values.

    Attributes
	settings that apply to an entire package.  Introduction
	of an attribute that causes different deliveries by the client could
    	cause a conflict with the versioning algebra, so we attempt to avoid
    	them.

    Tags
	settings that affect individual files within a package.

Attribute and tag syntax and namespace
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Syntax
``````

Naming
^^^^^^

    The syntax for attributes and tags is similar to that used for
    |pkg7| and |smf7| FMRIs.

    [org_prefix,][name][:locale]

    The organizational prefix is a forward-ordered or reverse-ordered
    domain name, followed by a comma.  The name field is an identifier
    which may have a prefix ending in a period to allocate the namespace.
    If the locale field is omitted, the default locale is "C", a 7-bit
    ASCII locale.

    Each of these fields is [A-Za-z][A-Za-z0-9\_-.]*.

Manifests
^^^^^^^^^

    In package manifests, the syntax for an attribute is:

       set name=<attribute name> value=<value> [value=<value2> ...]

    In package manifests, tags are included in the action line
    for the action they apply to:

       <action> [...] <tag name>=<tag value> [<tag name>=<tag value2> ...]

Unprefixed attributes and tags
``````````````````````````````

    All unprefixed attributes and tags are reserved for use by the
    framework.

    Generally, unprefixed tags are introduced in the definition of an
    action.

Attributes and tags common to all packages
``````````````````````````````````````````

    Attributes and tags starting with "pkg." or "info." are for attributes
    common to all packages, regardless of which particular OS platforms that
    a specific package may target.   "pkg" attributes are used by the 
    packaging system itself, while "info" attributes are purely informational,
    possibly for use by other software.

Common attributes
^^^^^^^^^^^^^^^^^

    pkg.summary
       A short, descriptive name for the package.
       pkg.summary:fr would be the descriptive name in French.
       Exact numerical version strings are discouraged in the
       descriptive name.

       Example:  "Apache HTTPD Web Server 2.x"

    pkg.description
       A descriptive paragraph for the package.  Exact numerical version
       strings can be embedded in the paragraph.

    pkg.detailed-url
       One or more URLs to pages or sites with further information about
       the package. pkg.detailed-url:fr would be the URL to a page with
       information in French.

    pkg.renamed
       A value of "true" indicates the package has been renamed or split
       into the packages listed in the depend actions.

    pkg.obsolete
       A value of "true" indicates the package is obsolete and should be
       removed on upgrade.

    pkg.human-version
       For components whose upstream version isn't a dot-separated sequence
       of nonnegative integers (OpenSSL's 0.9.8r, for example), this
       attribute can be set to that string, and will be displayed when
       appropriate.  It cannot be used in an FMRI to install a particular
       version; package authors must still convert the version into a
       sequence of integers.

    variant.*
       See facets.txt

Common tags
^^^^^^^^^^^

    disable_fmri
       See "Actuators" section of |pkg7|

    facet.*
       See facets.txt

    reboot-needed
       See "Actuators" section of |pkg7|

    refresh_fmri
       See "Actuators" section of |pkg7|

    restart_fmri
       See "Actuators" section of |pkg7|

    suspend_fmri
       See "Actuators" section of |pkg7|

    variant.*
       See facets.txt

Informational attributes
^^^^^^^^^^^^^^^^^^^^^^^^

The following attributes are not necessary for correct package installation,
but having a shared convention lowers confusion between publishers and
users.

info.classification
    A list of labels classifying the package into the categories
    shared among |pkg7| graphical clients.

    Values currently used for OpenSolaris are prefixed with
    ``org.opensolaris.category.2008:`` and must match one of the
    categories listed in ``src/gui/data/opensolaris.org.sections``

info.keyword
    A list of additional terms that should cause this package to be
    returned by a search.

info.maintainer
    A human readable string describing the entity providing the
    package.  For an individual, this string is expected to be their
    name, or name and email.

info.maintainer-url
    A URL associated with the entity providing the package.

info.upstream
    A human readable string describing the entity that creates the
    software.  For an individual, this string is expected to be
    their name, or name and email.

info.upstream-url
    A URL associated with the entity that creates the 
    software delivered within the package.

info.source-url
    A URL to the source code bundle, if appropriate, for the package.

info.repository-url
    A URL to the source code repository, if appropriate, for the
    package.

info.repository-changeset
    A changeset ID for the version of the source code contained in
    info.repository-url.

Attributes common to all packages for an OS platform
````````````````````````````````````````````````````

    Each OS platform is expected to define a string representing that
    platform.  For example, the |OS_Name| platform is represented by
    the string "opensolaris".

OpenSolaris attributes
^^^^^^^^^^^^^^^^^^^^^^

    org.opensolaris.arc-caseid
        One or more case identifiers (e.g., PSARC/2008/190) associated with
        the ARC case or cases associated with the component(s) delivered by
        the package.

    org.opensolaris.smf.fmri
	One or more FMRI's representing SMF services delivered by this
	package.  Automatically generated by |pkgdepend1| for packages
	containing SMF service manifests.

    opensolaris.zone
	Obsolete - replaced by variant.opensolaris.zone.

    variant.opensolaris.zone
	See facets.txt

OpenSolaris tags
^^^^^^^^^^^^^^^^

    opensolaris.zone
	Obsolete - replaced by variant.opensolaris.zone.

    variant.opensolaris.zone
	See facets.txt

Organization specific attributes
````````````````````````````````

    Organizations wishing to provide a package with additional metadata
    or to amend an existing package's metadata (in a repository that
    they have control over) must use an organization-specific prefix.
    For example, a service organization might introduce
    ``service.example.com,support-level`` or
    ``com.example.service,support-level`` to describe a level of support
    for a package and its contents.

Attributes specific to certain types of actions
```````````````````````````````````````````````

    Each type of action also has specific attributes covered in the 
    documentation of those actions.   These are generally documented 
    in the section of the |pkg7| manual page for that action.

Attributes specific to certain types of file
````````````````````````````````````````````

    These would generally appear on file actions for files in a specific
    format.

    elfarch, elfbits, elfhash

	Data about ELF format binary files (may be renamed in the future
	to info.file.elf.*).   Automatically generated during package 
	publication.  See the "File Actions" section of |pkg7|.

    info.file.font.name

	The name of a font contained in a given file.   There may be multiple
	values per file for formats which collect multiple typefaces into a
	single file, such as .ttc (TrueType Collections), or for font aliases.
	May also be provided in localized variants, such as a Chinese font 
	providing both info.file.font.name:en and info.file.font.name:zh for
	the English and	Chinese names for the font.

    info.file.font.xlfd

	An X Logical Font Description (XLFD) for a font contained in a
	given file.   Should match an XLFD listed in fonts.dir or fonts.alias
	for the file.  There may be multiple values per file due to font
	aliases.


.. 3.3.  Attributes best avoided

.. built-on release

.. One problem we may run into is packages that have been built on a
    release newer than that on the image.  These packages should be
    evaluated as unsuitable for the image, and not offered in the graph.
    There are a few ways to handle this case:

..    1.  Separate repository.  All packages offered by a repository were
        built on a known system configuration.  This change requires
        negotiation between client and server for a built-on match
        condition.  It also means that multiple repositories are needed
        for a long lifecycle distribution.

..    2.  Attributes.  Each package comes with a built-on attribute.  This
        means that clients move from one built-on release to another
        triggered by conditions set by the base package on the client.
        Another drawback is that it becomes impossible to request a
        specific package by an FMRI, without additional knowledge.

..   3.  Additional version specifier.  We could extend
        release,branch:timestamp to release,built,branch:timestamp--or
        fold the built and branch version together.  Since the built
        portion must reserve major.minor.micro, that means we move to a
        package FMRI that looks like

..        coreutils@6.7,5.11.0.1:timestamp

..        This choice looks awkward.  We could instead treat the built
        portion as having a default value on a particular client.  Then
        the common specifier would be

..        name@release[,build]-branch:timestamp

..        build would be the highest available valid release for the
        image.

..    The meaning of the built-on version could be controversial.  A
    simple approach would be to base it on base/minimal's release,
    rather than uname(1) output.



