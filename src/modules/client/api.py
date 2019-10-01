#!/usr/bin/python3.5
#
# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
# or http://www.opensolaris.org/os/licensing.
# See the License for the specific language governing permissions
# and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at usr/src/OPENSOLARIS.LICENSE.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END
#

#
# Copyright (c) 2008, 2019, Oracle and/or its affiliates. All rights reserved.
# Copyright 2019 OmniOS Community Edition (OmniOSce) Association.
#

"""This module provides the supported, documented interface for clients to
interface with the pkg(5) system.

Refer to pkg.api_common and pkg.plandesc for additional core class
documentation.

Consumers should catch ApiException when calling any API function, and
may optionally catch any subclass of ApiException for further, specific
error handling.
"""

#
# this file is not completely pylint clean
#
# pylint: disable=C0111,C0301,C0321,E0702,R0201,W0102
# pylint: disable=W0212,W0511,W0612,W0613,W0702
#
# C0111 Missing docstring
# C0301 Line too long
# C0321 More than one statement on a single line
# E0702 Raising NoneType while only classes, instances or string are allowed
# R0201 Method could be a function
# W0102 Dangerous default value %s as argument
# W0212 Access to a protected member %s of a client class
# W0511 XXX
# W0612 Unused variable '%s'
# W0613 Unused argument '%s'
# W0702 No exception type(s) specified
#

import collections
import copy
import datetime
import errno
import fnmatch
import glob
import os
import shutil
import simplejson as json
import sys
import tempfile
import threading
import time
import re as relib
from functools import cmp_to_key

from six.moves.urllib.parse import unquote

import pkg.actions as actions
import six

import pkg.catalog as catalog
import pkg.client.api_errors as apx
import pkg.client.bootenv as bootenv
import pkg.client.history as history
import pkg.client.image as image
import pkg.client.imageconfig as imgcfg
import pkg.client.imageplan as imageplan
import pkg.client.imagetypes as imgtypes
import pkg.client.indexer as indexer
import pkg.client.pkgdefs as pkgdefs
import pkg.client.plandesc as plandesc
import pkg.client.publisher as publisher
import pkg.client.query_parser as query_p
import pkg.fmri as fmri
import pkg.mediator as med
import pkg.misc as misc
import pkg.nrlock
import pkg.p5i as p5i
import pkg.p5s as p5s
import pkg.portable as portable
import pkg.search_errors as search_errors
import pkg.version

from pkg.api_common import (PackageInfo, LicenseInfo, PackageCategory,
    _get_pkg_cat_data)
from pkg.client import global_settings
from pkg.client.debugvalues import DebugValues
from pkg.client.pkgdefs import * # pylint: disable=W0401
from pkg.smf import NonzeroExitException

# we import PlanDescription here even though it isn't used so that consumers
# of the api still have access to the class definition and are able to do
# things like help(pkg.client.api.PlanDescription)
from pkg.client.plandesc import PlanDescription # pylint: disable=W0611

CURRENT_API_VERSION = 83
COMPATIBLE_API_VERSIONS = frozenset([72, 73, 74, 75, 76, 77, 78, 79, 80, 81,
    82, CURRENT_API_VERSION])
CURRENT_P5I_VERSION = 1

# Image type constants.
IMG_TYPE_NONE = imgtypes.IMG_NONE # No image.
IMG_TYPE_ENTIRE = imgtypes.IMG_ENTIRE # Full image ('/').
IMG_TYPE_PARTIAL = imgtypes.IMG_PARTIAL  # Not yet implemented.
IMG_TYPE_USER = imgtypes.IMG_USER # Not '/'; some other location.

# History result constants.
RESULT_CANCELED = history.RESULT_CANCELED
RESULT_NOTHING_TO_DO = history.RESULT_NOTHING_TO_DO
RESULT_SUCCEEDED = history.RESULT_SUCCEEDED
RESULT_FAILED_BAD_REQUEST = history.RESULT_FAILED_BAD_REQUEST
RESULT_FAILED_CONFIGURATION = history.RESULT_FAILED_CONFIGURATION
RESULT_FAILED_CONSTRAINED = history.RESULT_FAILED_CONSTRAINED
RESULT_FAILED_LOCKED = history.RESULT_FAILED_LOCKED
RESULT_FAILED_SEARCH = history.RESULT_FAILED_SEARCH
RESULT_FAILED_STORAGE = history.RESULT_FAILED_STORAGE
RESULT_FAILED_TRANSPORT = history.RESULT_FAILED_TRANSPORT
RESULT_FAILED_ACTUATOR = history.RESULT_FAILED_ACTUATOR
RESULT_FAILED_OUTOFMEMORY = history.RESULT_FAILED_OUTOFMEMORY
RESULT_CONFLICTING_ACTIONS = history.RESULT_CONFLICTING_ACTIONS
RESULT_FAILED_UNKNOWN = history.RESULT_FAILED_UNKNOWN

AUTO_BE_NAME_TIME_PREFIX = "time:"

# Globals.
logger = global_settings.logger

class _LockedGenerator(object):
        """This is a private class and should not be used by API consumers.

        This decorator class wraps API generator functions, managing the
        activity and cancelation locks.  Due to implementation differences
        in the decorator protocol, the decorator must be used with
        parenthesis in order for this to function correctly.  Always
        decorate functions @_LockedGenerator()."""

        def __init__(self, *d_args, **d_kwargs):
                object.__init__(self)

        def __call__(self, f):
                def wrapper(*fargs, **f_kwargs):
                        instance, fargs = fargs[0], fargs[1:]
                        instance._acquire_activity_lock()
                        instance._enable_cancel()

                        clean_exit = True
                        canceled = False
                        try:
                                for v in f(instance, *fargs, **f_kwargs):
                                        yield v
                        except GeneratorExit:
                                return
                        except apx.CanceledException:
                                canceled = True
                                raise
                        except Exception:
                                clean_exit = False
                                raise
                        finally:
                                if canceled:
                                        instance._cancel_done()
                                elif clean_exit:
                                        try:
                                                instance._disable_cancel()
                                        except apx.CanceledException:
                                                instance._cancel_done()
                                                instance._activity_lock.release()
                                                raise
                                else:
                                        instance._cancel_cleanup_exception()
                                instance._activity_lock.release()

                return wrapper


class _LockedCancelable(object):
        """This is a private class and should not be used by API consumers.

        This decorator class wraps non-generator cancelable API functions,
        managing the activity and cancelation locks.  Due to implementation
        differences in the decorator protocol, the decorator must be used with
        parenthesis in order for this to function correctly.  Always
        decorate functions @_LockedCancelable()."""

        def __init__(self, *d_args, **d_kwargs):
                object.__init__(self)

        def __call__(self, f):
                def wrapper(*fargs, **f_kwargs):
                        instance, fargs = fargs[0], fargs[1:]
                        instance._acquire_activity_lock()
                        instance._enable_cancel()

                        clean_exit = True
                        canceled = False
                        try:
                                return f(instance, *fargs, **f_kwargs)
                        except apx.CanceledException:
                                canceled = True
                                raise
                        except Exception:
                                clean_exit = False
                                raise
                        finally:
                                instance._img.cleanup_downloads()
                                try:
                                        if int(os.environ.get("PKG_DUMP_STATS",
                                            0)) > 0:
                                                instance._img.transport.stats.dump()
                                except ValueError:
                                        # Don't generate stats if an invalid
                                        # value is supplied.
                                        pass

                                if canceled:
                                        instance._cancel_done()
                                elif clean_exit:
                                        try:
                                                instance._disable_cancel()
                                        except apx.CanceledException:
                                                instance._cancel_done()
                                                # if f() acquired the image
                                                # lock, drop it
                                                if instance._img.locked:
                                                        instance._img.unlock()
                                                instance._activity_lock.release()
                                                raise
                                else:
                                        instance._cancel_cleanup_exception()
                                # if f() acquired the image lock, drop it
                                if instance._img.locked:
                                        instance._img.unlock()
                                instance._activity_lock.release()

                return wrapper


class ImageInterface(object):
        """This class presents an interface to images that clients may use.
        There is a specific order of methods which must be used to install
        or uninstall packages, or update an image.  First, a gen_plan_* method
        must be called.  After that method completes successfully, describe may
        be called, and prepare must be called.  Finally, execute_plan may be
        called to implement the previous created plan.  The other methods
        do not have an ordering imposed upon them, and may be used as
        needed.  Cancel may only be invoked while a cancelable method is
        running."""

        FACET_ALL = 0
        FACET_IMAGE = 1
        FACET_INSTALLED = 2

        FACET_SRC_SYSTEM = pkg.facet.Facets.FACET_SRC_SYSTEM
        FACET_SRC_LOCAL = pkg.facet.Facets.FACET_SRC_LOCAL
        FACET_SRC_PARENT = pkg.facet.Facets.FACET_SRC_PARENT

        # Constants used to reference specific values that info can return.
        INFO_FOUND = 0
        INFO_MISSING = 1
        INFO_ILLEGALS = 3

        LIST_ALL = 0
        LIST_INSTALLED = 1
        LIST_INSTALLED_NEWEST = 2
        LIST_NEWEST = 3
        LIST_UPGRADABLE = 4

        MATCH_EXACT = 0
        MATCH_FMRI = 1
        MATCH_GLOB = 2

        VARIANT_ALL = 0
        VARIANT_ALL_POSSIBLE = 1
        VARIANT_IMAGE = 2
        VARIANT_IMAGE_POSSIBLE = 3
        VARIANT_INSTALLED = 4
        VARIANT_INSTALLED_POSSIBLE = 5

        def __init__(self, img_path, version_id, progresstracker,
            cancel_state_callable, pkg_client_name, exact_match=True,
            cmdpath=None):
                """Constructs an ImageInterface object.

                'img_path' is the absolute path to an existing image or to a
                path from which to start looking for an image.  To control this
                behaviour use the 'exact_match' parameter.

                'version_id' indicates the version of the api the client is
                expecting to use.

                'progresstracker' is the ProgressTracker object the client wants
                the api to use for UI progress callbacks.

                'cancel_state_callable' is an optional function reference that
                will be called if the cancellable status of an operation
                changes.

                'pkg_client_name' is a string containing the name of the client,
                such as "pkg".

                'exact_match' is a boolean indicating whether the API should
                attempt to find a usable image starting from the specified
                directory, going up to the filesystem root until it finds one.
                If set to True, an image must exist at the location indicated
                by 'img_path'.
                """

                if version_id not in COMPATIBLE_API_VERSIONS:
                        raise apx.VersionException(CURRENT_API_VERSION,
                            version_id)

                if sys.path[0].startswith("/dev/fd/"):
                        #
                        # Normally when the kernel forks off an interpreted
                        # program, it executes the interpreter with the first
                        # argument being the path to the interpreted program
                        # we're executing.  But in the case of suid scripts
                        # this presents a security problem because that path
                        # could be updated after exec but before the
                        # interpreter opens reads the program.  To avoid this
                        # race, for suid script the kernel replaces the name
                        # of the interpreted program with /dev/fd/###, and
                        # opens the interpreted program such that it can be
                        # read from the specified file descriptor device node.
                        # So if we detect that path[0] (which should be then
                        # interpreted program name) is a /dev/fd/ path, that
                        # means we're being run as an suid script, which we
                        # don't really want to support.  (Since this breaks
                        # our subsequent code that attempt to determine the
                        # name of the executable we are running as.)
                        #
                        raise apx.SuidUnsupportedError()

                # The image's History object will use client_name from
                # global_settings, but if the program forgot to set it,
                # we'll go ahead and do so here.
                if global_settings.client_name is None:
                        global_settings.client_name = pkg_client_name

                if cmdpath is None:
                        cmdpath = misc.api_cmdpath()
                self.cmdpath = cmdpath

                # prevent brokeness in the test suite
                if self.cmdpath and \
                    "PKG_NO_RUNPY_CMDPATH" in os.environ and \
                    self.cmdpath.endswith(os.sep + "run.py"):
                        raise RuntimeError("""
An ImageInterface object was allocated from within ipkg test suite and
cmdpath was not explicitly overridden.  Please make sure to set
explicitly set cmdpath when allocating an ImageInterface object, or
override cmdpath when allocating an Image object by setting PKG_CMDPATH
in the environment or by setting simulate_cmdpath in DebugValues.""")

                if isinstance(img_path, six.string_types):
                        # Store this for reset().
                        self._img_path = img_path
                        self._img = image.Image(img_path,
                            progtrack=progresstracker,
                            user_provided_dir=exact_match,
                            cmdpath=self.cmdpath)

                        # Store final image path.
                        self._img_path = self._img.get_root()
                elif isinstance(img_path, image.Image):
                        # This is a temporary, special case for client.py
                        # until the image api is complete.
                        self._img = img_path
                        self._img_path = img_path.get_root()
                else:
                        # API consumer passed an unknown type for img_path.
                        raise TypeError(_("Unknown img_path type."))

                self.__progresstracker = progresstracker
                lin = None
                if self._img.linked.ischild():
                        lin = self._img.linked.child_name
                self.__progresstracker.set_linked_name(lin)

                self.__cancel_state_callable = cancel_state_callable
                self.__plan_type = None
                self.__api_op = None
                self.__plan_desc = None
                self.__planned_children = False
                self.__prepared = False
                self.__executed = False
                self.__be_activate = True
                self.__backup_be_name = None
                self.__be_name = None
                self.__can_be_canceled = False
                self.__canceling = False
                self._activity_lock = pkg.nrlock.NRLock()
                self.__blocking_locks = False
                self._img.blocking_locks = self.__blocking_locks
                self.__cancel_lock = pkg.nrlock.NRLock()
                self.__cancel_cv = threading.Condition(self.__cancel_lock)
                self.__backup_be = None # create if needed
                self.__new_be = None # create if needed
                self.__alt_sources = {}

        def __set_blocking_locks(self, value):
                self._activity_lock.acquire()
                self.__blocking_locks = value
                self._img.blocking_locks = value
                self._activity_lock.release()

        def __set_img_alt_sources(self, repos):
                """Private helper function to change image to use alternate
                package sources if applicable."""

                # When using alternate package sources with the image, the
                # result is a composite of the package data already known
                # by the image and the alternate sources.
                if repos:
                        self._img.set_alt_pkg_sources(
                            self.__get_alt_pkg_data(repos))
                else:
                        self._img.set_alt_pkg_sources(None)

        @_LockedCancelable()
        def set_alt_repos(self, repos):
                """Public function to specify alternate package sources."""
                self.__set_img_alt_sources(repos)

        blocking_locks = property(lambda self: self.__blocking_locks,
            __set_blocking_locks, doc="A boolean value indicating whether "
            "the API should wait until the image interface can be locked if "
            "it is in use by another thread or process.  Clients should be "
            "aware that there is no timeout mechanism in place if blocking is "
            "enabled, and so should take steps to remain responsive to user "
            "input or provide a way for users to cancel operations.")

        @property
        def excludes(self):
                """The list of excludes for the image."""
                return self._img.list_excludes()

        @property
        def img(self):
                """Private; public access to this property will be removed at
                a later date.  Do not use."""
                return self._img

        @property
        def img_type(self):
                """Returns the IMG_TYPE constant for the image's type."""
                if not self._img:
                        return None
                return self._img.image_type(self._img.root)

        @property
        def is_liveroot(self):
                """A boolean indicating whether the image to be modified is
                for the live system root."""
                return self._img.is_liveroot()

        @property
        def is_zone(self):
                """A boolean value indicating whether the image is a zone."""
                return self._img.is_zone()

        @property
        def is_active_liveroot_be(self):
                """A boolean indicating whether the image to be modified is
                the active BE for the system's root image."""

                if not self._img.is_liveroot():
                        return False

                try:
                        be_name, be_uuid = bootenv.BootEnv.get_be_name(
                            self._img.root)
                        return be_name == \
                            bootenv.BootEnv.get_activated_be_name()
                except apx.BEException:
                        # If boot environment logic isn't supported, return
                        # False.  This is necessary for user images and for
                        # the test suite.
                        return False

        @property
        def img_plandir(self):
                """A path to the image planning directory."""
                plandir = self._img.plandir
                misc.makedirs(plandir)
                return plandir

        @property
        def last_modified(self):
                """A datetime object representing when the image's metadata was
                last updated."""

                return self._img.get_last_modified()

        def __set_progresstracker(self, value):
                self._activity_lock.acquire()
                self.__progresstracker = value

                # tell the progress tracker about this image's name
                lin = None
                if self._img.linked.ischild():
                        lin = self._img.linked.child_name
                self.__progresstracker.set_linked_name(lin)

                self._activity_lock.release()

        progresstracker = property(lambda self: self.__progresstracker,
            __set_progresstracker, doc="The current ProgressTracker object.  "
            "This value should only be set when no other API calls are in "
            "progress.")

        @property
        def mediators(self):
                """A dictionary of the mediators and their configured version
                and implementation of the form:

                   {
                       mediator-name: {
                           "version": mediator-version-string,
                           "version-source": (site|vendor|system|local),
                           "implementation": mediator-implementation-string,
                           "implementation-source": (site|vendor|system|local),
                       }
                   }

                  'version' is an optional string that specifies the version
                   (expressed as a dot-separated sequence of non-negative
                   integers) of the mediator for use.

                   'version-source' is a string describing the source of the
                   selected version configuration.  It indicates how the
                   version component of the mediation was selected.

                   'implementation' is an optional string that specifies the
                   implementation of the mediator for use in addition to or
                   instead of 'version'.

                   'implementation-source' is a string describing the source of
                   the selected implementation configuration.  It indicates how
                   the implementation component of the mediation was selected.
                 """

                ret = {}
                for m, mvalues in six.iteritems(self._img.cfg.mediators):
                        ret[m] = copy.copy(mvalues)
                        if "version" in ret[m]:
                                # Don't expose internal Version object to
                                # external consumers.
                                ret[m]["version"] = \
                                    ret[m]["version"].get_short_version()
                        if "implementation-version" in ret[m]:
                                # Don't expose internal Version object to
                                # external consumers.
                                ret[m]["implementation-version"] = \
                                    ret[m]["implementation-version"].get_short_version()
                return ret

        @property
        def root(self):
                """The absolute pathname of the filesystem root of the image.
                This property is read-only."""
                if not self._img:
                        return None
                return self._img.root

        @staticmethod
        def check_be_name(be_name):
                bootenv.BootEnv.check_be_name(be_name)
                return True

        def __cert_verify(self, log_op_end=None):
                """Verify validity of certificates.  Any apx.ExpiringCertificate
                exceptions are caught here, a message is displayed, and
                execution continues.

                All other exceptions will be passed to the calling context.
                The caller can also set log_op_end to a list of exceptions
                that should result in a call to self.log_operation_end()
                before the exception is passed on.
                """

                if log_op_end is None:
                        log_op_end = []

                # we always explicitly handle apx.ExpiringCertificate
                assert apx.ExpiringCertificate not in log_op_end

                try:
                        self._img.check_cert_validity()
                except apx.ExpiringCertificate as e:
                        logger.warning(e)
                except:
                        exc_type, exc_value, exc_traceback = sys.exc_info()
                        if exc_type in log_op_end:
                                self.log_operation_end(error=exc_value)
                        raise

        def __refresh_publishers(self):
                """Refresh publisher metadata; this should only be used by
                functions in this module for implicit refresh cases."""

                #
                # Verify validity of certificates before possibly
                # attempting network operations.
                #
                self.__cert_verify()
                try:
                        self._img.refresh_publishers(immediate=True,
                            progtrack=self.__progresstracker)
                except apx.ImageFormatUpdateNeeded:
                        # If image format update is needed to perform refresh,
                        # continue on and allow failure to happen later since
                        # an implicit refresh failing for this reason isn't
                        # important.  (This allows planning installs and updates
                        # before the format of an image is updated.  Yes, this
                        # means that if the refresh was needed to do that, then
                        # this isn't useful, but this is as good as it gets.)
                        logger.warning(_("Skipping publisher metadata refresh;"
                            "image rooted at {0} must have its format updated "
                            "before a refresh can occur.").format(self._img.root))

        def _acquire_activity_lock(self):
                """Private helper method to aqcuire activity lock."""

                rc = self._activity_lock.acquire(
                    blocking=self.__blocking_locks)
                if not rc:
                        raise apx.ImageLockedError()

        def __plan_common_start(self, operation, noexecute, backup_be,
            backup_be_name, new_be, be_name, be_activate):
                """Start planning an operation:
                    Acquire locks.
                    Log the start of the operation.
                    Check be_name."""

                self._acquire_activity_lock()
                try:
                        self._enable_cancel()
                        if self.__plan_type is not None:
                                raise apx.PlanExistsException(
                                    self.__plan_type)
                        self._img.lock(allow_unprivileged=noexecute)
                except:
                        self._cancel_cleanup_exception()
                        self._activity_lock.release()
                        raise

                assert self._activity_lock._is_owned()
                self.log_operation_start(operation)
                self.__backup_be = backup_be
                self.__backup_be_name = backup_be_name
                self.__new_be = new_be
                self.__be_activate = be_activate
                self.__be_name = be_name
                for val in (self.__be_name, self.__backup_be_name):
                        if val is not None:
                                self.check_be_name(val)
                                if not self._img.is_liveroot():
                                        self._cancel_cleanup_exception()
                                        self._activity_lock.release()
                                        self._img.unlock()
                                        raise apx.BENameGivenOnDeadBE(val)

        def __plan_common_finish(self):
                """Finish planning an operation."""

                assert self._activity_lock._is_owned()
                self._img.cleanup_downloads()
                self._img.unlock()
                try:
                        if int(os.environ.get("PKG_DUMP_STATS", 0)) > 0:
                                self._img.transport.stats.dump()
                except ValueError:
                        # Don't generate stats if an invalid value
                        # is supplied.
                        pass

                self._activity_lock.release()

        def __auto_be_name(self):
                try:
                        be_template = self._img.cfg.get_property(
                            'property', imgcfg.AUTO_BE_NAME)
                except:
                        be_template = None

                if not be_template or len(be_template) == 0:
                        return

                if be_template.startswith(AUTO_BE_NAME_TIME_PREFIX):
                        try:
                                be_template = time.strftime(
                                    be_template[len(AUTO_BE_NAME_TIME_PREFIX):])
                        except:
                                return
                else:
                        release = date = None
                        # Check to see if release/name is being updated
                        for src, dest in self._img.imageplan.plan_desc:
                                if (not dest or
                                    dest.get_name() != 'release/name'):
                                        continue
                                # It is, extract attributes
                                for a in self._img.imageplan.pd.update_actions:
                                        if not isinstance(a.dst,
                                            actions.attribute.AttributeAction):
                                                continue
                                        name = a.dst.attrs['name']
                                        if name == 'ooce.release':
                                                release = a.dst.attrs['value']
                                        elif name == 'ooce.release.build':
                                                date = a.dst.attrs['value']
                                        if release and date:
                                                break
                                break

                        if not release and not date:
                                # No variables changed in this update
                                return

                        if '%r' in be_template and not release:
                                return
                        if '%d' in be_template and not date:
                                return
                        if '%D' in be_template and not date:
                                return
                        if release:
                                be_template = be_template.replace('%r', release)
                        if date:
                                be_template = be_template.replace('%d', date)
                                be_template = be_template.replace('%D',
                                    date.replace('.', ''))

                if not be_template or len(be_template) == 0:
                        return

                be = bootenv.BootEnv(self._img)
                self.__be_name = be.get_new_be_name(new_bename=be_template)

        def __set_be_creation(self):
                """Figure out whether or not we'd create a new or backup boot
                environment given inputs and plan.  Toss cookies if we need a
                new be and can't have one."""

                if not self._img.is_liveroot():
                        self.__backup_be = False
                        self.__new_be = False
                        return

                if self.__new_be is None:
                        # If image policy requires a new BE or the plan requires
                        # it, then create a new BE.
                        self.__new_be = (self._img.cfg.get_policy_str(
                            imgcfg.BE_POLICY) == "always-new" or
                            self._img.imageplan.reboot_needed())
                elif self.__new_be is False and \
                    self._img.imageplan.reboot_needed():
                        raise apx.ImageUpdateOnLiveImageException()

                # If a new BE is required and no BE name has been provided
                # on the command line, attempt to determine a BE name
                # automatically.
                if self.__new_be == True and self.__be_name == None:
                        self.__auto_be_name()

                if not self.__new_be and self.__backup_be is None:
                        # Create a backup be if allowed by policy (note that the
                        # 'default' policy is currently an alias for
                        # 'create-backup') ...
                        allow_backup = self._img.cfg.get_policy_str(
                            imgcfg.BE_POLICY) in ("default",
                                "create-backup")

                        self.__backup_be = False
                        if allow_backup:
                                # ...when packages are being
                                # updated...
                                for src, dest in self._img.imageplan.plan_desc:
                                        if src and dest:
                                                self.__backup_be = True
                                                break
                        if allow_backup and not self.__backup_be:
                                # ...or if new packages that have
                                # reboot-needed=true are being
                                # installed.
                                self.__backup_be = \
                                    self._img.imageplan.reboot_advised()

        def abort(self, result=RESULT_FAILED_UNKNOWN):
                """Indicate that execution was unexpectedly aborted and log
                operation failure if possible."""
                try:
                        # This can raise if, for example, we're aborting
                        # because we have a PipeError and we can no longer
                        # write.  So suppress problems here.
                        if self.__progresstracker:
                                self.__progresstracker.flush()
                except:
                        pass

                self._img.history.abort(result)

        def avoid_pkgs(self, fmri_strings, unavoid=False):
                """Avoid/Unavoid one or more packages.  It is an error to
                avoid an installed package, or unavoid one that would
                be installed."""

                self._acquire_activity_lock()
                try:
                        if not unavoid:
                                self._img.avoid_pkgs(fmri_strings,
                                    progtrack=self.__progresstracker,
                                    check_cancel=self.__check_cancel)
                        else:
                                self._img.unavoid_pkgs(fmri_strings,
                                    progtrack=self.__progresstracker,
                                    check_cancel=self.__check_cancel)
                finally:
                        self._activity_lock.release()
                return True

        def gen_available_mediators(self):
                """A generator function that yields tuples of the form (mediator,
                mediations), where mediator is the name of the provided mediation
                and mediations is a list of dictionaries of possible mediations
                to set, provided by installed packages, of the form:

                   {
                       mediator-name: {
                           "version": mediator-version-string,
                           "version-source": (site|vendor|system|local),
                           "implementation": mediator-implementation-string,
                           "implementation-source": (site|vendor|system|local),
                       }
                   }

                  'version' is an optional string that specifies the version
                   (expressed as a dot-separated sequence of non-negative
                   integers) of the mediator for use.

                   'version-source' is a string describing how the version
                   component of the mediation will be evaluated during
                   mediation. (The priority.)

                   'implementation' is an optional string that specifies the
                   implementation of the mediator for use in addition to or
                   instead of 'version'.

                   'implementation-source' is a string describing how the
                   implementation component of the mediation will be evaluated
                   during mediation.  (The priority.)

                The list of possible mediations returned for each mediator is
                ordered by source in the sequence 'site', 'vendor', 'system',
                and then by version and implementation.  It does not include
                mediations that exist only in the image configuration.
                """

                ret = collections.defaultdict(set)
                excludes = self._img.list_excludes()
                for f in self._img.gen_installed_pkgs():
                        mfst = self._img.get_manifest(f)
                        for m, mediations in mfst.gen_mediators(
                            excludes=excludes):
                                ret[m].update(mediations)

                for mediator in sorted(ret):
                        for med_priority, med_ver, med_impl in sorted(
                            ret[mediator], key=cmp_to_key(med.cmp_mediations)):
                                val = {}
                                if med_ver:
                                        # Don't expose internal Version object
                                        # to callers.
                                        val["version"] = \
                                            med_ver.get_short_version()
                                if med_impl:
                                        val["implementation"] = med_impl

                                ret_priority = med_priority
                                if not ret_priority:
                                        # For consistency with the configured
                                        # case, list source as this.
                                        ret_priority = "system"
                                # Always set both to be consistent
                                # with @mediators.
                                val["version-source"] = ret_priority
                                val["implementation-source"] = \
                                    ret_priority
                                yield mediator, val

        def get_avoid_list(self):
                """Return list of tuples of (pkg stem, pkgs w/ group
                dependencies on this) """
                return [a for a in six.iteritems(self._img.get_avoid_dict())]

        def gen_facets(self, facet_list, implicit=False, patterns=misc.EmptyI):
                """A generator function that produces tuples of the form:

                    (
                        name,    - (string) facet name (e.g. facet.doc)
                        value    - (boolean) current facet value
                        src      - (string) source for the value
                        masked   - (boolean) is the facet maksed by another
                    )

                Results are always sorted by facet name.

                'facet_list' is one of the following constant values indicating
                which facets should be returned based on how they were set:

                        FACET_ALL
                                Return all facets set in the image and all
                                facets listed in installed packages.

                        FACET_IMAGE
                                Return only the facets set in the image.

                        FACET_INSTALLED
                                Return only the facets listed in installed
                                packages.

                'implicit' is a boolean indicating whether facets specified in
                the 'patterns' parameter that are not explicitly set in the
                image or found in a package should be included.  Ignored for
                FACET_INSTALLED case.

                'patterns' is an optional list of facet wildcard strings to
                filter results by."""

                facets = self._img.cfg.facets
                if facet_list != self.FACET_INSTALLED:
                        # Include all facets set in image.
                        fimg = set(facets.keys())
                else:
                        # Don't include any set only in image.
                        fimg = set()

                # Get all facets found in packages and determine state.
                fpkg = set()
                excludes = self._img.list_excludes()
                if facet_list != self.FACET_IMAGE:
                        for f in self._img.gen_installed_pkgs():
                                # The manifest must be loaded without
                                # pre-applying excludes so that gen_facets() can
                                # choose how to filter the actions.
                                mfst = self._img.get_manifest(f,
                                    ignore_excludes=True)
                                for facet in mfst.gen_facets(excludes=excludes):
                                        # Use Facets object to determine
                                        # effective facet state.
                                        fpkg.add(facet)

                # If caller wants implicit values, include non-glob patterns
                # (even if not found) in results unless only installed facets
                # were requested.
                iset = set()
                if implicit and facet_list != self.FACET_INSTALLED:
                        iset = set(
                            p.startswith("facet.") and p or ("facet." + p)
                            for p in patterns
                            if "*" not in p and "?" not in p
                        )
                flist = sorted(fimg | fpkg | iset)

                # Generate the results.
                for name in misc.yield_matching("facet.", flist, patterns):
                        # check if the facet is explicitly set.
                        if name not in facets:
                                # The image's Facets dictionary will return
                                # the effective value for any facets not
                                # explicitly set in the image (wildcards or
                                # implicit). _match_src() will tell us how
                                # that effective value was determined (via a
                                # local or inherited wildcard facet, or via a
                                # system default).
                                src = facets._match_src(name)
                                yield (name, facets[name], src, False)
                                continue

                        # This is an explicitly set facet.
                        for value, src, masked in facets._src_values(name):
                                yield (name, value, src, masked)

        def gen_variants(self, variant_list, implicit=False,
            patterns=misc.EmptyI):
                """A generator function that produces tuples of the form:

                    (
                        name,    - (string) variant name (e.g. variant.arch)
                        value    - (string) current variant value,
                        possible - (list) list of possible variant values based
                                   on installed packages; empty unless using
                                   *_POSSIBLE variant_list.
                    )

                Results are always sorted by variant name.

                'variant_list' is one of the following constant values indicating
                which variants should be returned based on how they were set:

                        VARIANT_ALL
                                Return all variants set in the image and all
                                variants listed in installed packages.

                        VARIANT_ALL_POSSIBLE
                                Return possible variant values (those found in
                                any installed package) for all variants set in
                                the image and all variants listed in installed
                                packages.

                        VARIANT_IMAGE
                                Return only the variants set in the image.

                        VARIANT_IMAGE_POSSIBLE
                                Return possible variant values (those found in
                                any installed package) for only the variants set
                                in the image.

                        VARIANT_INSTALLED
                                Return only the variants listed in installed
                                packages.

                        VARIANT_INSTALLED_POSSIBLE
                                Return possible variant values (those found in
                                any installed package) for only the variants
                                listed in installed packages.

                'implicit' is a boolean indicating whether variants specified in
                the 'patterns' parameter that are not explicitly set in the
                image or found in a package should be included.  Ignored for
                VARIANT_INSTALLED* cases.

                'patterns' is an optional list of variant wildcard strings to
                filter results by."""

                variants = self._img.cfg.variants
                if variant_list != self.VARIANT_INSTALLED and \
                    variant_list != self.VARIANT_INSTALLED_POSSIBLE:
                        # Include all variants set in image.
                        vimg = set(variants.keys())
                else:
                        # Don't include any set only in image.
                        vimg = set()

                # Get all variants found in packages and determine state.
                vpkg = {}
                excludes = self._img.list_excludes()
                vposs = collections.defaultdict(set)
                if variant_list != self.VARIANT_IMAGE:
                        # Only incur the overhead of reading through all
                        # installed packages if not just listing variants set in
                        # image or listing possible values for them.
                        for f in self._img.gen_installed_pkgs():
                                # The manifest must be loaded without
                                # pre-applying excludes so that gen_variants()
                                # can choose how to filter the actions.
                                mfst = self._img.get_manifest(f,
                                    ignore_excludes=True)
                                for variant, vals in mfst.gen_variants(
                                    excludes=excludes):
                                        if variant not in vimg:
                                                # Although rare, packages with
                                                # unknown variants (those not
                                                # set in the image) can be
                                                # installed as long as content
                                                # does not conflict.  For those
                                                # variants, return None.  This
                                                # is done without using get() as
                                                # that would cause None to be
                                                # returned for implicitly set
                                                # variants (e.g. debug).
                                                try:
                                                        vpkg[variant] = \
                                                            variants[variant]
                                                except KeyError:
                                                        vpkg[variant] = None

                                        if (variant_list == \
                                            self.VARIANT_ALL_POSSIBLE or
                                            variant_list == \
                                                self.VARIANT_IMAGE_POSSIBLE or
                                            variant_list == \
                                                self.VARIANT_INSTALLED_POSSIBLE):
                                                # Build possible list of variant
                                                # values.
                                                vposs[variant].update(set(vals))

                # If caller wants implicit values, include non-glob debug
                # patterns (even if not found) in results unless only installed
                # variants were requested.
                iset = set()
                if implicit and variant_list != self.VARIANT_INSTALLED and \
                    variant_list != self.VARIANT_INSTALLED_POSSIBLE:
                        # Normalize patterns.
                        iset = set(
                            p.startswith("variant.") and p or ("variant." + p)
                            for p in patterns
                            if "*" not in p and "?" not in p
                        )
                        # Only debug variants can have an implicit value.
                        iset = set(
                            p
                            for p in iset
                            if p.startswith("variant.debug.")
                        )
                vlist = sorted(vimg | set(vpkg.keys()) | iset)

                # Generate the results.
                for name in misc.yield_matching("variant.", vlist, patterns):
                        try:
                                yield (name, vpkg[name], sorted(vposs[name]))
                        except KeyError:
                                yield (name, variants[name],
                                    sorted(vposs[name]))

        def freeze_pkgs(self, fmri_strings, dry_run=False, comment=None,
            unfreeze=False):
                """Freeze/Unfreeze one or more packages."""

                # Comment is only a valid parameter if a freeze is happening.
                assert not comment or not unfreeze

                self._acquire_activity_lock()
                try:
                        if unfreeze:
                                return self._img.unfreeze_pkgs(fmri_strings,
                                    progtrack=self.__progresstracker,
                                    check_cancel=self.__check_cancel,
                                    dry_run=dry_run)
                        else:
                                return self._img.freeze_pkgs(fmri_strings,
                                    progtrack=self.__progresstracker,
                                    check_cancel=self.__check_cancel,
                                    dry_run=dry_run, comment=comment)
                finally:
                        self._activity_lock.release()

        def get_frozen_list(self):
                """Return list of tuples of (pkg fmri, reason package was
                frozen, timestamp when package was frozen)."""

                return self._img.get_frozen_list()

        def __plan_common_exception(self, log_op_end_all=False):
                """Deal with exceptions that can occur while planning an
                operation.  Any exceptions generated here are passed
                onto the calling context.  By default all exceptions
                will result in a call to self.log_operation_end() before
                they are passed onto the calling context."""

                exc_type, exc_value, exc_traceback = sys.exc_info()

                if exc_type == apx.PlanCreationException:
                        self.__set_history_PlanCreationException(exc_value)
                elif exc_type == apx.CanceledException:
                        self._cancel_done()
                elif exc_type == apx.ConflictingActionErrors:
                        self.log_operation_end(error=str(exc_value),
                            result=RESULT_CONFLICTING_ACTIONS)
                elif exc_type in [
                    apx.IpkgOutOfDateException,
                    fmri.IllegalFmri]:
                        self.log_operation_end(error=exc_value)
                elif log_op_end_all:
                        self.log_operation_end(error=exc_value)

                if exc_type != apx.ImageLockedError:
                        # Must be called before reset_unlock, and only if
                        # the exception was not a locked error.
                        self._img.unlock()

                try:
                        if int(os.environ.get("PKG_DUMP_STATS", 0)) > 0:
                                self._img.transport.stats.dump()
                except ValueError:
                        # Don't generate stats if an invalid value
                        # is supplied.
                        pass

                # In the case of duplicate actions, we want to save off the plan
                # description for display to the client (if they requested it),
                # as once the solver's done its job, there's interesting
                # information in the plan.  We have to save it here and restore
                # it later because __reset_unlock() torches it.
                if exc_type == apx.ConflictingActionErrors:
                        self._img.imageplan.set_be_options(self.__backup_be,
                            self.__backup_be_name, self.__new_be,
                            self.__be_activate, self.__be_name)
                        plan_desc = self._img.imageplan.describe()

                self.__reset_unlock()

                if exc_type == apx.ConflictingActionErrors:
                        self.__plan_desc = plan_desc

                self._activity_lock.release()

                # re-raise the original exception. (we have to explicitly
                # restate the original exception since we may have cleared the
                # current exception scope above.)
                six.reraise(exc_type, exc_value, exc_traceback)

        def solaris_image(self):
                """Returns True if the current image is a solaris image, or an
                image which contains the pkg(5) packaging system."""

                # First check to see if the special package "release/name"
                # exists and contains metadata saying this is Solaris.
                results = self.__get_pkg_list(self.LIST_INSTALLED,
                    patterns=["release/name"], return_fmris=True)
                results = [e for e in results]
                if results:
                        pfmri, summary, categories, states, attrs = results[0]
                        mfst = self._img.get_manifest(pfmri)
                        osname = mfst.get("pkg.release.osname", None)
                        if osname == "sunos":
                                return True

                # Otherwise, see if we can find package/pkg (or SUNWipkg) and
                # system/core-os (or SUNWcs).
                results = self.__get_pkg_list(self.LIST_INSTALLED,
                    patterns=["/package/pkg", "SUNWipkg", "/system/core-os",
                        "SUNWcs"])
                installed = set(e[0][1] for e in results)
                if ("SUNWcs" in installed or "system/core-os" in installed) and \
                    ("SUNWipkg" in installed or "package/pkg" in installed):
                        return True

                return False

        def __ipkg_require_latest(self, noexecute):
                """Raises an IpkgOutOfDateException if the current image
                contains the pkg(5) packaging system and a newer version
                of the pkg(5) packaging system is installable."""

                # Skip this check on OmniOS. If, in the future, we need
                # to deliver an update to pkg(5) which will make it essential
                # to have the latest in advance, we will adjust this.
                return

                if not self.solaris_image():
                        return

                # Get old purpose in order to be able to restore it on return.
                p = self.__progresstracker.get_purpose()

                try:
                        #
                        # Let progress tracker know that subsequent callbacks
                        # into it will all be in service of update checking.
                        # Note that even though this might return, the
                        # finally: will still reset the purpose.
                        #
                        self.__progresstracker.set_purpose(
                            self.__progresstracker.PURPOSE_PKG_UPDATE_CHK)
                        if self._img.ipkg_is_up_to_date(
                            self.__check_cancel, noexecute,
                            refresh_allowed=False,
                            progtrack=self.__progresstracker):
                                return
                except apx.ImageNotFoundException:
                        # Can't do anything in this
                        # case; so proceed.
                        return
                finally:
                        self.__progresstracker.set_purpose(p)

                raise apx.IpkgOutOfDateException()

        def __verify_args(self, args):
                """Verifies arguments passed into the API.
                It tests for correct data types of the input args, verifies that
                passed in FMRIs are valid, checks if repository URIs are valid
                and does some logical tests for the combination of arguments."""

                arg_types = {
                    # arg name              type                   nullable
                    "_act_timeout":         (int,                  False),
                    "_be_activate":         (bool,                 False),
                    "_be_name":             (six.string_types,     True),
                    "_backup_be":           (bool,                 True),
                    "_backup_be_name":      (six.string_types,     True),
                    "_ignore_missing":      (bool,                 False),
                    "_ipkg_require_latest": (bool,                 False),
                    "_li_erecurse":         (iter,                 True),
                    "_li_ignore":           (iter,                 True),
                    "_li_md_only":          (bool,                 False),
                    "_li_parent_sync":      (bool,                 False),
                    "_new_be":              (bool,                 True),
                    "_noexecute":           (bool,                 False),
                    "_pubcheck":            (bool,                 False),
                    "_refresh_catalogs":    (bool,                 False),
                    "_repos":               (iter,                 True),
                    "_update_index":        (bool,                 False),
                    "facets":               (dict,                 True),
                    "mediators":            (iter,                 True),
                    "pkgs_inst":            (iter,                 True),
                    "pkgs_to_uninstall":    (iter,                 True),
                    "pkgs_update":          (iter,                 True),
                    "reject_list":          (iter,                 True),
                    "variants":             (dict,                 True),
                }

                # merge kwargs into the main arg dict
                if "kwargs" in args:
                        for name, value in args["kwargs"].items():
                                args[name] = value

                # check arguments for proper type and nullability
                for a in args:
                        try:
                                a_type, nullable = arg_types[a]
                        except KeyError:
                                # unknown argument passed, ignore
                                continue

                        assert nullable or args[a] is not None

                        if args[a] is not None and a_type == iter:
                                try:
                                        iter(args[a])
                                except TypeError:
                                        raise AssertionError("{0} is not an "
                                            "iterable".format(a))

                        else:
                                assert (args[a] is None or
                                    isinstance(args[a], a_type)), "{0} is " \
                                    "type {1}; expected {2}".format(a, type(a),
                                    a_type)

                # check if passed FMRIs are valid
                illegals = []
                for i in ("pkgs_inst", "pkgs_update", "pkgs_to_uninstall",
                    "reject_list"):
                        try:
                                fmris = args[i]
                        except KeyError:
                                continue
                        if fmris is None:
                                continue
                        for pat, err, pfmri, matcher in \
                            self.parse_fmri_patterns(fmris):
                                if not err:
                                        continue
                                else:
                                        illegals.append(fmris)

                if illegals:
                        raise apx.PlanCreationException(illegal=illegals)

                # some logical checks
                errors = []
                if not args["_new_be"] and args["_be_name"]:
                        errors.append(apx.InvalidOptionError(
                            apx.InvalidOptionError.REQUIRED, ["_be_name",
                            "_new_be"]))
                if not args["_backup_be"] and args["_backup_be_name"]:
                        errors.append(apx.InvalidOptionError(
                            apx.InvalidOptionError.REQUIRED, ["_backup_be_name",
                            "_backup_be"]))
                if args["_backup_be"] and args["_new_be"]:
                        errors.append(apx.InvalidOptionError(
                            apx.InvalidOptionError.INCOMPAT, ["_backup_be",
                            "_new_be"]))

                if errors:
                        raise apx.InvalidOptionErrors(errors)

                # check if repo URIs are valid
                try:
                        repos = args["_repos"]
                except KeyError:
                        return

                if not repos:
                        return

                illegals = []
                for r in repos:
                        valid = False
                        if type(r) == publisher.RepositoryURI:
                                # RepoURI objects pass right away
                                continue

                        if not misc.valid_pub_url(r):
                                illegals.append(r)

                if illegals:
                        raise apx.UnsupportedRepositoryURI(illegals)

        def __plan_op(self, _op, _act_timeout=0, _ad_kwargs=None,
            _backup_be=None, _backup_be_name=None, _be_activate=True,
            _be_name=None, _ipkg_require_latest=False, _li_ignore=None,
            _li_erecurse=None, _li_md_only=False, _li_parent_sync=True,
            _new_be=False, _noexecute=False, _pubcheck=True,
            _refresh_catalogs=True, _repos=None, _update_index=True, **kwargs):
                """Contructs a plan to change the package or linked image
                state of an image.

                We can raise PermissionsException, PlanCreationException,
                InventoryException, or LinkedImageException.

                Arguments prefixed with '_' are primarily used within this
                function.  All other arguments must be specified via keyword
                assignment and will be passed directly on to the image
                interfaces being invoked."

                '_op' is the API operation we will perform.

                '_ad_kwargs' is only used dyring attach or detach and it
                is a dictionary of arguments that will be passed to the
                linked image attach/detach interfaces.

                '_ipkg_require_latest' enables a check to verify that the
                latest installable version of the pkg(5) packaging system is
                installed before we proceed with the requested operation.

                For all other '_' prefixed parameters, please refer to the
                'gen_plan_*' functions which invoke this function for an
                explanation of their usage and effects.

                This function first yields the plan description for the global
                zone, then either a series of dictionaries representing the
                parsable output from operating on the child images or a series
                of None values."""

                # sanity checks
                assert _op in api_op_values
                assert _ad_kwargs is None or \
                    _op in [API_OP_ATTACH, API_OP_DETACH]
                assert _ad_kwargs != None or \
                    _op not in [API_OP_ATTACH, API_OP_DETACH]
                assert not _li_md_only or \
                    _op in [API_OP_ATTACH, API_OP_DETACH, API_OP_SYNC]
                assert not _li_md_only or _li_parent_sync

                self.__verify_args(locals())

                # make some perf optimizations
                if _li_md_only:
                        _refresh_catalogs = _update_index = False
                if _op in [API_OP_DETACH, API_OP_SET_MEDIATOR, API_OP_FIX,
                    API_OP_VERIFY, API_OP_DEHYDRATE, API_OP_REHYDRATE]:
                        # these operations don't change fmris and don't need
                        # to recurse, so disable a bunch of linked image
                        # operations.
                        _li_parent_sync = False
                        _pubcheck = False
                        _li_ignore = [] # ignore all children

                # All the image interface functions that we invoke have some
                # common arguments.  Set those up now.
                args_common = {}
                args_common["op"] = _op
                args_common["progtrack"] = self.__progresstracker
                args_common["check_cancel"] = self.__check_cancel
                args_common["noexecute"] = _noexecute

                # make sure there is no overlap between the common arguments
                # supplied to all api interfaces and the arguments that the
                # api arguments that caller passed to this function.
                assert (set(args_common) & set(kwargs)) == set(), \
                    "{0} & {1} != set()".format(str(set(args_common)),
                    str(set(kwargs)))
                kwargs.update(args_common)

                # Lock the current image.
                self.__plan_common_start(_op, _noexecute, _backup_be,
                    _backup_be_name, _new_be, _be_name, _be_activate)

                try:
                        if _op == API_OP_ATTACH:
                                self._img.linked.attach_parent(**_ad_kwargs)
                        elif _op == API_OP_DETACH:
                                self._img.linked.detach_parent(**_ad_kwargs)

                        if _li_parent_sync:
                                # refresh linked image data from parent image.
                                self._img.linked.syncmd_from_parent()

                        # initialize recursion state
                        self._img.linked.api_recurse_init(
                                li_ignore=_li_ignore, repos=_repos)

                        if _pubcheck:
                                # check that linked image pubs are in sync
                                self.__linked_pubcheck(_op)

                        if _refresh_catalogs:
                                self.__refresh_publishers()

                        if _ipkg_require_latest:
                                # If this is an image update then make
                                # sure the latest version of the ipkg
                                # software is installed.
                                self.__ipkg_require_latest(_noexecute)

                        self.__set_img_alt_sources(_repos)

                        if _li_md_only:
                                self._img.make_noop_plan(**args_common)
                        elif _op in [API_OP_ATTACH, API_OP_DETACH, API_OP_SYNC]:
                                self._img.make_sync_plan(**kwargs)
                        elif _op in [API_OP_CHANGE_FACET,
                            API_OP_CHANGE_VARIANT]:
                                self._img.make_change_varcets_plan(**kwargs)
                        elif _op == API_OP_DEHYDRATE:
                                self._img.make_dehydrate_plan(**kwargs)
                        elif _op == API_OP_INSTALL or \
                            _op == API_OP_EXACT_INSTALL:
                                self._img.make_install_plan(**kwargs)
                        elif _op in [API_OP_FIX, API_OP_VERIFY]:
                                self._img.make_fix_plan(**kwargs)
                        elif _op == API_OP_REHYDRATE:
                                self._img.make_rehydrate_plan(**kwargs)
                        elif _op == API_OP_REVERT:
                                self._img.make_revert_plan(**kwargs)
                        elif _op == API_OP_SET_MEDIATOR:
                                self._img.make_set_mediators_plan(**kwargs)
                        elif _op == API_OP_UNINSTALL:
                                self._img.make_uninstall_plan(**kwargs)
                        elif _op == API_OP_UPDATE:
                                self._img.make_update_plan(**kwargs)
                        else:
                                raise RuntimeError(
                                    "Unknown api op: {0}".format(_op))

                        self.__api_op = _op

                        if self._img.imageplan.nothingtodo():
                                # no package changes mean no index changes
                                _update_index = False

                        self._disable_cancel()
                        self.__set_be_creation()
                        self._img.imageplan.set_be_options(
                            self.__backup_be, self.__backup_be_name,
                            self.__new_be, self.__be_activate, self.__be_name)
                        self.__plan_desc = self._img.imageplan.describe()
                        if not _noexecute:
                                self.__plan_type = self.__plan_desc.plan_type

                        if _act_timeout != 0:
                                self.__plan_desc.set_actuator_timeout(
                                    _act_timeout)

                        # Yield to our caller so they can display our plan
                        # before we recurse into child images.  Drop the
                        # activity lock before yielding because otherwise the
                        # caller can't do things like set the displayed
                        # license state for pkg plans).
                        self._activity_lock.release()
                        yield self.__plan_desc
                        self._activity_lock.acquire()

                        # plan operation in child images.  This currently yields
                        # either a dictionary representing the parsable output
                        # from the child image operation, or None.  Eventually
                        # these will yield plan descriptions objects instead.

                        for p_dict in self._img.linked.api_recurse_plan(
                            api_kwargs=kwargs, erecurse_list=_li_erecurse,
                            refresh_catalogs=_refresh_catalogs,
                            update_index=_update_index,
                            progtrack=self.__progresstracker):
                                yield p_dict

                        self.__planned_children = True

                except:
                        if _op in [
                            API_OP_UPDATE,
                            API_OP_INSTALL,
                            API_OP_REVERT,
                            API_OP_SYNC]:
                                self.__plan_common_exception(
                                    log_op_end_all=True)
                        else:
                                self.__plan_common_exception()
                        # NOTREACHED

                stuff_to_do = not self.planned_nothingtodo()

                if not stuff_to_do or _noexecute:
                        self.log_operation_end(
                            result=RESULT_NOTHING_TO_DO)

                self._img.imageplan.update_index = _update_index
                self.__plan_common_finish()

                # Value 'DebugValues' is unsubscriptable;
                # pylint: disable=E1136
                if DebugValues["plandesc_validate"]:
                        # save, load, and get a new json copy of the plan,
                        # then compare that new copy against our current one.
                        # this regressions tests the plan save/load code.
                        pd_json1 = self.__plan_desc.getstate(self.__plan_desc,
                            reset_volatiles=True)
                        fobj = tempfile.TemporaryFile(mode="w+")
                        json.dump(pd_json1, fobj, encoding="utf-8")
                        pd_new = plandesc.PlanDescription(_op)
                        pd_new._load(fobj)
                        pd_json2 = pd_new.getstate(pd_new, reset_volatiles=True)
                        fobj.close()
                        del fobj, pd_new
                        pkg.misc.json_diff("PlanDescription", \
                            pd_json1, pd_json2, pd_json1, pd_json2)
                        del pd_json1, pd_json2

        @_LockedCancelable()
        def load_plan(self, plan, prepared=False):
                """Load a previously generated PlanDescription."""

                # Prevent loading a plan if one has been already.
                if self.__plan_type is not None:
                        raise apx.PlanExistsException(self.__plan_type)

                # grab image lock.  we don't worry about dropping the image
                # lock since __activity_lock will drop it for us us after we
                # return (or if we generate an exception).
                self._img.lock()

                # load the plan
                self.__plan_desc = plan
                self.__plan_type = plan.plan_type
                self.__planned_children = True
                self.__prepared = prepared

                # load BE related plan settings
                self.__new_be = plan.new_be
                self.__be_activate = plan.activate_be
                self.__be_name = plan.be_name

                # sanity check: verify the BE name
                if self.__be_name is not None:
                        self.check_be_name(self.__be_name)
                        if not self._img.is_liveroot():
                                raise apx.BENameGivenOnDeadBE(self.__be_name)

                # sanity check: verify that all the fmris in the plan are in
                # the known catalog
                pkg_cat = self._img.get_catalog(self._img.IMG_CATALOG_KNOWN)
                for pp in plan.pkg_plans:
                        if pp.destination_fmri:
                                assert pkg_cat.get_entry(pp.destination_fmri), \
                                     "fmri part of plan, but currently " \
                                     "unknown: {0}".format(pp.destination_fmri)

                # allocate an image plan based on the supplied plan
                self._img.imageplan = imageplan.ImagePlan(self._img, plan._op,
                    self.__progresstracker, check_cancel=self.__check_cancel,
                    pd=plan)

                if prepared:
                        self._img.imageplan.skip_preexecute()

                # create a history entry
                self.log_operation_start(plan.plan_type)

        def __linked_pubcheck(self, api_op=None):
                """Private interface to perform publisher check on this image
                and its children."""

                if api_op in [API_OP_DETACH, API_OP_SET_MEDIATOR]:
                        # we don't need to do a pubcheck for detach or
                        # changing mediators
                        return

                # check the current image
                self._img.linked.pubcheck()

                # check child images
                self._img.linked.api_recurse_pubcheck(self.__progresstracker)

        @_LockedCancelable()
        def linked_publisher_check(self):
                """If we're a child image, verify that the parent image's
                publisher configuration is a subset of the child image's
                publisher configuration.  If we have any children, recurse
                into them and perform a publisher check."""

                # grab image lock.  we don't worry about dropping the image
                # lock since __activity_lock will drop it for us us after we
                # return (or if we generate an exception).
                self._img.lock(allow_unprivileged=True)

                # get ready to recurse
                self._img.linked.api_recurse_init()

                # check that linked image pubs are in sync
                self.__linked_pubcheck()

        def planned_nothingtodo(self, li_ignore_all=False):
                """Once an operation has been planned check if there is
                something todo.

                Callers should pass all arguments by name assignment and
                not by positional order.

                'li_ignore_all' indicates if we should only report on work
                todo in the parent image.  (i.e., if an operation was planned
                and that operation only involves changes to children, and
                li_ignore_all is true, then we'll report that there's nothing
                todo."""

                if not self._img.imageplan:
                        # if theres no plan there nothing to do
                        return True
                if not self._img.imageplan.nothingtodo():
                        return False
                if not self._img.linked.nothingtodo():
                        return False
                if not li_ignore_all:
                        assert self.__planned_children
                        if not self._img.linked.recurse_nothingtodo():
                                return False
                return True

        def plan_update(self, pkg_list, refresh_catalogs=True,
            reject_list=misc.EmptyI, noexecute=False, update_index=True,
            be_name=None, new_be=False, repos=None, be_activate=True):
                """DEPRECATED.  use gen_plan_update()."""
                for pd in self.gen_plan_update(
                    pkgs_update=pkg_list, refresh_catalogs=refresh_catalogs,
                    reject_list=reject_list, noexecute=noexecute,
                    update_index=update_index, be_name=be_name, new_be=new_be,
                    repos=repos, be_activate=be_activate):
                        continue
                return not self.planned_nothingtodo()

        def plan_update_all(self, refresh_catalogs=True,
            reject_list=misc.EmptyI, noexecute=False, force=False,
            update_index=True, be_name=None, new_be=True, repos=None,
            be_activate=True):
                """DEPRECATED.  use gen_plan_update()."""
                for pd in self.gen_plan_update(
                    refresh_catalogs=refresh_catalogs, reject_list=reject_list,
                    noexecute=noexecute, force=force,
                    update_index=update_index, be_name=be_name, new_be=new_be,
                    repos=repos, be_activate=be_activate):
                        continue
                return (not self.planned_nothingtodo(), self.solaris_image())

        def gen_plan_update(self, pkgs_update=None, act_timeout=0,
            backup_be=None, backup_be_name=None, be_activate=True, be_name=None,
            force=False, ignore_missing=False, li_ignore=None,
            li_parent_sync=True, li_erecurse=None, new_be=True, noexecute=False,
            pubcheck=True, refresh_catalogs=True, reject_list=misc.EmptyI,
            repos=None, update_index=True):

                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                If pkgs_update is not set, constructs a plan to update all
                packages on the system to the latest known versions.  Once an
                operation has been planned, it may be executed by first
                calling prepare(), and then execute_plan().  After execution
                of a plan, or to abandon a plan, reset() should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                If 'pkgs_update' is set, constructs a plan to update the
                packages provided in pkgs_update.

                Once an operation has been planned, it may be executed by
                first calling prepare(), and then execute_plan().

                'force' indicates whether update should skip the package
                system up to date check.

                'ignore_missing' indicates whether update should ignore packages
                which are not installed.

                'pubcheck' indicates that we should skip the child image
                publisher check before creating a plan for this image.  only
                pkg.1 should use this parameter, other callers should never
                specify it.

                For all other parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                if pkgs_update or force:
                        ipkg_require_latest = False
                else:
                        ipkg_require_latest = True

                op = API_OP_UPDATE
                return self.__plan_op(op,
                    _act_timeout=act_timeout, _backup_be=backup_be,
                    _backup_be_name=backup_be_name, _be_activate=be_activate,
                    _be_name=be_name, _ipkg_require_latest=ipkg_require_latest,
                    _li_ignore=li_ignore, _li_parent_sync=li_parent_sync,
                    _li_erecurse=li_erecurse, _new_be=new_be,
                    _noexecute=noexecute, _pubcheck=pubcheck,
                    _refresh_catalogs=refresh_catalogs, _repos=repos,
                    _update_index=update_index, ignore_missing=ignore_missing,
                    pkgs_update=pkgs_update, reject_list=reject_list,
                    )

        def plan_install(self, pkg_list, refresh_catalogs=True,
            noexecute=False, update_index=True, be_name=None,
            reject_list=misc.EmptyI, new_be=False, repos=None,
            be_activate=True):
                """DEPRECATED.  use gen_plan_install()."""
                for pd in self.gen_plan_install(
                     pkgs_inst=pkg_list, refresh_catalogs=refresh_catalogs,
                     noexecute=noexecute, update_index=update_index,
                     be_name=be_name, reject_list=reject_list, new_be=new_be,
                     repos=repos, be_activate=be_activate):
                        continue
                return not self.planned_nothingtodo()

        def gen_plan_install(self, pkgs_inst, act_timeout=0, backup_be=None,
            backup_be_name=None, be_activate=True, be_name=None,
            li_erecurse=None, li_ignore=None, li_parent_sync=True, new_be=False,
            noexecute=False, pubcheck=True, refresh_catalogs=True,
            reject_list=misc.EmptyI, repos=None, update_index=True):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Constructs a plan to install the packages provided in
                pkgs_inst.  Once an operation has been planned, it may be
                executed by first calling prepare(), and then execute_plan().
                After execution of a plan, or to abandon a plan, reset()
                should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                'act_timeout' sets the timeout for synchronous actuators in
                seconds, -1 is no timeout, 0 is for using asynchronous
                actuators.

                'backup_be' indicates whether a backup boot environment should
                be created before the operation is executed.  If True, a backup
                boot environment will be created.  If False, a backup boot
                environment will not be created. If None and a new boot
                environment is not created, and packages are being updated or
                are being installed and tagged with reboot-needed, a backup
                boot environment will be created.

                'backup_be_name' is a string to use as the name of any backup
                boot environment created during the operation.

                'be_activate' is an optional boolean indicating whether any
                new boot environment created for the operation should be set
                as the active one on next boot if the operation is successful.

                'be_name' is a string to use as the name of any new boot
                environment created during the operation.

                'li_erecurse' is either None or a list. If it's None (the
                default), the planning operation will not explicitly recurse
                into linked children to perform the requested operation. If this
                is a list of linked image children names, the requested
                operation will be performed in each of the specified
                children.

                'li_ignore' is either None or a list.  If it's None (the
                default), the planning operation will attempt to keep all
                linked children in sync.  If it's an empty list the planning
                operation will ignore all children.  If this is a list of
                linked image children names, those children will be ignored
                during the planning operation.  If a child is ignored during
                the planning phase it will also be skipped during the
                preparation and execution phases.

                'li_parent_sync' if the current image is a child image, this
                flag controls whether the linked image parent metadata will be
                automatically refreshed.

                'new_be' indicates whether a new boot environment should be
                created during the operation.  If True, a new boot environment
                will be created.  If False, and a new boot environment is
                needed, an ImageUpdateOnLiveImageException will be raised.
                If None, a new boot environment will be created only if needed.

                'noexecute' determines whether the resulting plan can be
                executed and whether history will be recorded after
                planning is finished.

                'pkgs_inst' is a list of packages to install.

                'refresh_catalogs' controls whether the catalogs will
                automatically be refreshed.

                'reject_list' is a list of patterns not to be permitted
                in solution; installed packages matching these patterns
                are removed.

                'repos' is a list of URI strings or RepositoryURI objects that
                represent the locations of additional sources of package data to
                use during the planned operation.  All API functions called
                while a plan is still active will use this package data.

                'update_index' determines whether client search indexes
                will be updated after operation completion during plan
                execution."""

                # certain parameters must be specified
                assert pkgs_inst and type(pkgs_inst) == list

                op = API_OP_INSTALL
                return self.__plan_op(op, _act_timeout=act_timeout,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_erecurse=li_erecurse, _li_ignore=li_ignore,
                    _li_parent_sync=li_parent_sync, _new_be=new_be,
                    _noexecute=noexecute, _pubcheck=pubcheck,
                    _refresh_catalogs=refresh_catalogs, _repos=repos,
                    _update_index=update_index, pkgs_inst=pkgs_inst,
                    reject_list=reject_list, )

        def gen_plan_exact_install(self, pkgs_inst, backup_be=None,
            backup_be_name=None, be_activate=True, be_name=None, li_ignore=None,
            li_parent_sync=True, new_be=False, noexecute=False,
            refresh_catalogs=True, reject_list=misc.EmptyI, repos=None,
            update_index=True):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Constructs a plan to install exactly the packages provided in
                pkgs_inst.  Once an operation has been planned, it may be
                executed by first calling prepare(), and then execute_plan().
                After execution of a plan, or to abandon a plan, reset()
                should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                'pkgs_inst' is a list of packages to install exactly.

                For all other parameters, refer to 'gen_plan_install'
                for an explanation of their usage and effects."""

                # certain parameters must be specified
                assert pkgs_inst and type(pkgs_inst) == list

                op = API_OP_EXACT_INSTALL
                return self.__plan_op(op,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_ignore=li_ignore, _li_parent_sync=li_parent_sync,
                    _new_be=new_be, _noexecute=noexecute,
                    _refresh_catalogs=refresh_catalogs, _repos=repos,
                    _update_index=update_index, pkgs_inst=pkgs_inst,
                    reject_list=reject_list)

        def gen_plan_sync(self, backup_be=None, backup_be_name=None,
            be_activate=True, be_name=None, li_ignore=None, li_md_only=False,
            li_parent_sync=True, li_pkg_updates=True, new_be=False,
            noexecute=False, pubcheck=True, refresh_catalogs=True,
            reject_list=misc.EmptyI, repos=None, update_index=True):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Constructs a plan to sync the current image with its
                linked image constraints.  Once an operation has been planned,
                it may be executed by first calling prepare(), and then
                execute_plan().  After execution of a plan, or to abandon a
                plan, reset() should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                'li_md_only' don't actually modify any packages in the current
                images, only sync the linked image metadata from the parent
                image.  If this options is True, 'li_parent_sync' must also be
                True.

                'li_pkg_updates' when planning a sync operation, allow updates
                to packages other than the constraints package.  If this
                option is False, planning a sync will fail if any packages
                (other than the constraints package) need updating to bring
                the image in sync with its parent.

                For all other parameters, refer to 'gen_plan_install' and
                'gen_plan_update' for an explanation of their usage and
                effects."""

                # we should only be invoked on a child image.
                if not self.ischild():
                        raise apx.LinkedImageException(
                            self_not_child=self._img_path)

                op = API_OP_SYNC
                return self.__plan_op(op,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_ignore=li_ignore, _li_md_only=li_md_only,
                    _li_parent_sync=li_parent_sync, _new_be=new_be,
                    _noexecute=noexecute, _pubcheck=pubcheck,
                    _refresh_catalogs=refresh_catalogs,
                    _repos=repos,
                    _update_index=update_index,
                    li_pkg_updates=li_pkg_updates, reject_list=reject_list)

        def gen_plan_attach(self, lin, li_path, allow_relink=False,
            backup_be=None, backup_be_name=None, be_activate=True, be_name=None,
            force=False, li_ignore=None, li_md_only=False, li_pkg_updates=True,
            li_props=None, new_be=False, noexecute=False, refresh_catalogs=True,
            reject_list=misc.EmptyI, repos=None, update_index=True):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Attach a parent image and sync the packages in the current
                image with the new parent.  Once an operation has been
                planned, it may be executed by first calling prepare(), and
                then execute_plan().  After execution of a plan, or to abandon
                a plan, reset() should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                'lin' a LinkedImageName object that is a name for the current
                image.

                'li_path' a path to the parent image.

                'allow_relink' allows re-linking of an image that is already a
                linked image child.  If this option is True we'll overwrite
                all existing linked image metadata.

                'li_props' optional linked image properties to apply to the
                child image.

                For all other parameters, refer to the 'gen_plan_install' and
                'gen_plan_sync' functions for an explanation of their usage
                and effects."""

                if li_props is None:
                        li_props = dict()

                op = API_OP_ATTACH
                ad_kwargs = {
                    "allow_relink": allow_relink,
                    "force": force,
                    "lin": lin,
                    "path": li_path,
                    "props": li_props,
                }
                return self.__plan_op(op,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_ignore=li_ignore, _li_md_only=li_md_only,
                    _new_be=new_be, _noexecute=noexecute,
                    _refresh_catalogs=refresh_catalogs, _repos=repos,
                    _update_index=update_index, _ad_kwargs=ad_kwargs,
                    li_pkg_updates=li_pkg_updates, reject_list=reject_list)

        def gen_plan_detach(self, backup_be=None,
            backup_be_name=None, be_activate=True, be_name=None, force=False,
            li_ignore=None, li_md_only=False, li_pkg_updates=True, new_be=False,
            noexecute=False):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Detach from a parent image and remove any constraints
                package from this image.  Once an operation has been planned,
                it may be executed by first calling prepare(), and then
                execute_plan().  After execution of a plan, or to abandon a
                plan, reset() should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                For all other parameters, refer to the 'gen_plan_install' and
                'gen_plan_sync' functions for an explanation of their usage
                and effects."""

                op = API_OP_DETACH
                ad_kwargs = {
                    "force": force
                }
                return self.__plan_op(op, _ad_kwargs=ad_kwargs,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_ignore=li_ignore, _li_md_only=li_md_only,
                    _new_be=new_be, _noexecute=noexecute,
                    _refresh_catalogs=False, _update_index=False,
                    li_pkg_updates=li_pkg_updates)

        def plan_uninstall(self, pkg_list, noexecute=False, update_index=True,
            be_name=None, new_be=False, be_activate=True):
                """DEPRECATED.  use gen_plan_uninstall()."""
                for pd in self.gen_plan_uninstall(pkgs_to_uninstall=pkg_list,
                    noexecute=noexecute, update_index=update_index,
                    be_name=be_name, new_be=new_be, be_activate=be_activate):
                        continue
                return not self.planned_nothingtodo()

        def gen_plan_uninstall(self, pkgs_to_uninstall, act_timeout=0,
            backup_be=None, backup_be_name=None, be_activate=True,
            be_name=None, ignore_missing=False, li_ignore=None,
            li_parent_sync=True, li_erecurse=None, new_be=False, noexecute=False,
            pubcheck=True, update_index=True):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Constructs a plan to remove the packages provided in
                pkgs_to_uninstall.  Once an operation has been planned, it may
                be executed by first calling prepare(), and then
                execute_plan().  After execution of a plan, or to abandon a
                plan, reset() should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                'ignore_missing' indicates whether uninstall should ignore
                packages which are not installed.

                'pkgs_to_uninstall' is a list of packages to uninstall.

                For all other parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                # certain parameters must be specified
                assert pkgs_to_uninstall and type(pkgs_to_uninstall) == list

                op = API_OP_UNINSTALL
                return self.__plan_op(op, _act_timeout=act_timeout,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_erecurse=li_erecurse, _li_ignore=li_ignore,
                    _li_parent_sync=li_parent_sync, _new_be=new_be,
                    _noexecute=noexecute, _pubcheck=pubcheck,
                    _refresh_catalogs=False, _update_index=update_index,
                    ignore_missing=ignore_missing,
                    pkgs_to_uninstall=pkgs_to_uninstall)

        def gen_plan_set_mediators(self, mediators, backup_be=None,
            backup_be_name=None, be_activate=True, be_name=None, li_ignore=None,
            li_parent_sync=True, new_be=None, noexecute=False,
            update_index=True):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Creates a plan to change the version and implementation values
                for mediators as specified in the provided dictionary.  Once an
                operation has been planned, it may be executed by first calling
                prepare(), and then execute_plan().  After execution of a plan,
                or to abandon a plan, reset() should be called.

                Callers should pass all arguments by name assignment and not by
                positional order.

                'mediators' is a dict of dicts of the mediators to set version
                and implementation for.  If the dict for a given mediator-name
                is empty, it will be interpreted as a request to revert the
                specified mediator to the default, "optimal" mediation.  It
                should be of the form:

                   {
                       mediator-name: {
                           "implementation": mediator-implementation-string,
                           "version": mediator-version-string
                       }
                   }

                   'implementation' is an optional string that specifies the
                   implementation of the mediator for use in addition to or
                   instead of 'version'.

                   'version' is an optional string that specifies the version
                   (expressed as a dot-separated sequence of non-negative
                   integers) of the mediator for use.

                For all other parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                assert mediators
                return self.__plan_op(API_OP_SET_MEDIATOR,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_ignore=li_ignore, _li_parent_sync=li_parent_sync,
                    mediators=mediators, _new_be=new_be, _noexecute=noexecute,
                    _refresh_catalogs=False, _update_index=update_index)

        def plan_change_varcets(self, variants=None, facets=None,
            noexecute=False, be_name=None, new_be=None, repos=None,
            be_activate=True):
                """DEPRECATED.  use gen_plan_change_varcets()."""
                for pd in self.gen_plan_change_varcets(
                    variants=variants, facets=facets, noexecute=noexecute,
                    be_name=be_name, new_be=new_be, repos=repos,
                    be_activate=be_activate):
                        continue
                return not self.planned_nothingtodo()

        def gen_plan_change_varcets(self, facets=None, variants=None,
            act_timeout=0, backup_be=None, backup_be_name=None,
            be_activate=True, be_name=None, li_erecurse=None, li_ignore=None,
            li_parent_sync=True, new_be=None, noexecute=False, pubcheck=True,
            refresh_catalogs=True, reject_list=misc.EmptyI, repos=None,
            update_index=True):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Creates a plan to change the specified variants and/or
                facets for the image.  Once an operation has been planned, it
                may be executed by first calling prepare(), and then
                execute_plan().  After execution of a plan, or to abandon a
                plan, reset() should be called.

                Callers should pass all arguments by name assignment and
                not by positional order.

                'facets' is a dict of the facets to change the values of.

                'variants' is a dict of the variants to change the values of.

                For all other parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                # An empty facets dictionary is allowed because that's how to
                # unset all set facets.
                if not variants and facets is None:
                        raise ValueError("Nothing to do")

                invalid_names = []
                if variants:
                        op = API_OP_CHANGE_VARIANT
                        # Check whether '*' or '?' is in the input. Currently,
                        # change-variant does not accept globbing. Also check
                        # for whitespaces.
                        for variant in variants:
                                if "*" in variant or "?" in variant:
                                        raise apx.UnsupportedVariantGlobbing()
                                if not misc.valid_varcet_name(variant):
                                        invalid_names.append(variant)
                else:
                        op = API_OP_CHANGE_FACET
                        for facet in facets:
                                # Explict check for not None so that we can fix
                                # a broken system from the past by clearing
                                # the facet. Neither True of False should be
                                # allowed for this special facet.
                                if facet == "facet.version-lock.*" and \
                                    facets[facet] is not None:
                                        raise apx.UnsupportedFacetChange(facet,
                                            facets[facet])
                                if not misc.valid_varcet_name(facet):
                                        invalid_names.append(facet)
                if invalid_names:
                        raise apx.InvalidVarcetNames(invalid_names)

                return self.__plan_op(op, _act_timeout=act_timeout,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_activate=be_activate, _be_name=be_name,
                    _li_erecurse=li_erecurse, _li_ignore=li_ignore,
                    _li_parent_sync=li_parent_sync, _new_be=new_be,
                    _noexecute=noexecute, _pubcheck=pubcheck,
                    _refresh_catalogs=refresh_catalogs, _repos=repos,
                    _update_index=update_index, facets=facets,
                    variants=variants, reject_list=reject_list)

        def plan_revert(self, args, tagged=False, noexecute=True, be_name=None,
            new_be=None, be_activate=True):
                """DEPRECATED.  use gen_plan_revert()."""
                for pd in self.gen_plan_revert(
                    args=args, tagged=tagged, noexecute=noexecute,
                    be_name=be_name, new_be=new_be, be_activate=be_activate):
                        continue
                return not self.planned_nothingtodo()

        def gen_plan_revert(self, args, backup_be=None, backup_be_name=None,
            be_activate=True, be_name=None, new_be=None, noexecute=True,
            tagged=False):
                """This is a generator function that yields a PlanDescription
                object.  If parsable_version is set, it also yields dictionaries
                containing plan information for child images.

                Plan to revert either files or all files tagged with
                specified values.  Args contains either path names or tag
                names to be reverted, tagged is True if args contains tags.
                Once an operation has been planned, it may be executed by
                first calling prepare(), and then execute_plan().  After
                execution of a plan, or to abandon a plan, reset() should be
                called.

                For all other parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                op = API_OP_REVERT
                return self.__plan_op(op, _be_activate=be_activate,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_name=be_name, _li_ignore=[], _new_be=new_be,
                    _noexecute=noexecute, _refresh_catalogs=False,
                    _update_index=False, args=args, tagged=tagged)

        def gen_plan_dehydrate(self, publishers=None, noexecute=True):
                """This is a generator function that yields a PlanDescription
                object.

                Plan to remove non-editable files and hardlinks from an image.
                Once an operation has been planned, it may be executed by
                first calling prepare(), and then execute_plan().  After
                execution of a plan, or to abandon a plan, reset() should be
                called.

                'publishers' is a list of publishers to dehydrate.

                For all other parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                op = API_OP_DEHYDRATE
                return self.__plan_op(op, _noexecute=noexecute,
                    _refresh_catalogs=False, _update_index=False,
                    publishers=publishers)

        def gen_plan_rehydrate(self, publishers=None, noexecute=True):
                """This is a generator function that yields a PlanDescription
                object.

                Plan to reinstall non-editable files and hardlinks to a dehydrated
                image. Once an operation has been planned, it may be executed by
                first calling prepare(), and then execute_plan().  After
                execution of a plan, or to abandon a plan, reset() should be
                called.

                'publishers' is a list of publishers to dehydrate on.

                For all other parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                op = API_OP_REHYDRATE
                return self.__plan_op(op, _noexecute=noexecute,
                    _refresh_catalogs=False, _update_index=False,
                    publishers=publishers)

        def gen_plan_verify(self, args, noexecute=True, unpackaged=False,
            unpackaged_only=False, verify_paths=misc.EmptyI):
                """This is a generator function that yields a PlanDescription
                object.

                Plan to repair anything that fails to verify. Once an operation
                has been planned, it may be executed by first calling prepare(),
                and then execute_plan().  After execution of a plan, or to
                abandon a plan, reset() should be called.

                For parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                op = API_OP_VERIFY
                return self.__plan_op(op, args=args, _noexecute=noexecute,
                    _refresh_catalogs=False, _update_index=False, _new_be=None,
                    unpackaged=unpackaged, unpackaged_only=unpackaged_only,
                    verify_paths=verify_paths)

        def gen_plan_fix(self, args, backup_be=None, backup_be_name=None,
            be_activate=True, be_name=None, new_be=None, noexecute=True,
            unpackaged=False):
                """This is a generator function that yields a PlanDescription
                object.

                Plan to repair anything that fails to verify. Once an operation
                has been planned, it may be executed by first calling prepare(),
                and then execute_plan().  After execution of a plan, or to
                abandon a plan, reset() should be called.

                For parameters, refer to the 'gen_plan_install'
                function for an explanation of their usage and effects."""

                op = API_OP_FIX
                return self.__plan_op(op, args=args, _be_activate=be_activate,
                    _backup_be=backup_be, _backup_be_name=backup_be_name,
                    _be_name=be_name, _new_be=new_be, _noexecute=noexecute,
                    _refresh_catalogs=False, _update_index=False,
                    unpackaged=unpackaged)

        def attach_linked_child(self, lin, li_path, li_props=None,
            accept=False, allow_relink=False, force=False, li_md_only=False,
            li_pkg_updates=True, noexecute=False,
            refresh_catalogs=True, reject_list=misc.EmptyI,
            show_licenses=False, update_index=True):
                """Attach an image as a child to the current image (the
                current image will become a parent image. This operation
                results in attempting to sync the child image with the parent
                image.

                'lin' is the name of the child image

                'li_path' is the path to the child image

                'li_props' optional linked image properties to apply to the
                child image.

                'allow_relink' indicates whether we should allow linking of a
                child image that is already linked (the child may already
                be a child or a parent image).

                'force' indicates whether we should allow linking of a child
                image even if the specified linked image type doesn't support
                attaching of children.

                'li_md_only' indicates whether we should only update linked
                image metadata and not actually try to sync the child image.

                'li_pkg_updates' indicates whether we should disallow pkg
                updates during the child image sync.

                'noexecute' indicates if we should actually make any changes
                rather or just simulate the operation.

                'refresh_catalogs' controls whether the catalogs will
                automatically be refreshed.

                'reject_list' is a list of patterns not to be permitted
                in solution; installed packages matching these patterns
                are removed.

                'update_index' determines whether client search indexes will
                be updated in the child after the sync operation completes.

                This function returns a tuple of the format (rv, err) where rv
                is a pkg.client.pkgdefs return value and if an error was
                encountered err is an exception object which describes the
                error."""

                return self._img.linked.attach_child(lin, li_path, li_props,
                    accept=accept, allow_relink=allow_relink, force=force,
                    li_md_only=li_md_only, li_pkg_updates=li_pkg_updates,
                    noexecute=noexecute,
                    progtrack=self.__progresstracker,
                    refresh_catalogs=refresh_catalogs, reject_list=reject_list,
                    show_licenses=show_licenses, update_index=update_index)

        def detach_linked_children(self, li_list, force=False,
            li_md_only=False, li_pkg_updates=True, noexecute=False):
                """Detach one or more children from the current image. This
                operation results in the removal of any constraint package
                from the child images.

                'li_list' a list of linked image name objects which specified
                which children to operate on.  If the list is empty then we
                operate on all children.

                For all other parameters, refer to the 'attach_linked_child'
                function for an explanation of their usage and effects.

                This function returns a dictionary where the keys are linked
                image name objects and the values are the result of the
                specified operation on the associated child image.  The result
                is a tuple of the format (rv, err) where rv is a
                pkg.client.pkgdefs return value and if an error was
                encountered err is an exception object which describes the
                error."""

                return self._img.linked.detach_children(li_list,
                    force=force, li_md_only=li_md_only,
                    li_pkg_updates=li_pkg_updates,
                    noexecute=noexecute)

        def detach_linked_rvdict2rv(self, rvdict):
                """Convenience function that takes a dictionary returned from
                an operations on multiple children and merges the results into
                a single return code."""

                return self._img.linked.detach_rvdict2rv(rvdict)

        def sync_linked_children(self, li_list,
            accept=False, li_md_only=False,
            li_pkg_updates=True, noexecute=False,
            refresh_catalogs=True, show_licenses=False, update_index=True):
                """Sync one or more children of the current image.

                For all other parameters, refer to the 'attach_linked_child'
                and 'detach_linked_children' functions for an explanation of
                their usage and effects.

                For a description of the return value, refer to the
                'detach_linked_children' function."""

                rvdict = self._img.linked.sync_children(li_list,
                    accept=accept, li_md_only=li_md_only,
                    li_pkg_updates=li_pkg_updates, noexecute=noexecute,
                    progtrack=self.__progresstracker,
                    refresh_catalogs=refresh_catalogs,
                    show_licenses=show_licenses, update_index=update_index)
                return rvdict

        def sync_linked_rvdict2rv(self, rvdict):
                """Convenience function that takes a dictionary returned from
                an operations on multiple children and merges the results into
                a single return code."""

                return self._img.linked.sync_rvdict2rv(rvdict)

        def audit_linked_children(self, li_list):
                """Audit one or more children of the current image to see if
                they are in sync with this image.

                For all parameters, refer to the 'detach_linked_children'
                functions for an explanation of their usage and effects.

                For a description of the return value, refer to the
                'detach_linked_children' function."""

                rvdict = self._img.linked.audit_children(li_list)
                return rvdict

        def audit_linked_rvdict2rv(self, rvdict):
                """Convenience function that takes a dictionary returned from
                an operations on multiple children and merges the results into
                a single return code."""

                return self._img.linked.audit_rvdict2rv(rvdict)

        def audit_linked(self, li_parent_sync=True):
                """If the current image is a child image, this function
                audits the current image to see if it's in sync with it's
                parent.

                For a description of the return value, refer to the
                'detach_linked_children' function."""

                lin = self._img.linked.child_name
                rvdict = {}

                if li_parent_sync:
                        # refresh linked image data from parent image.
                        rvdict[lin] = self._img.linked.syncmd_from_parent(
                            catch_exception=True)
                        if rvdict[lin] is not None:
                                return rvdict

                rvdict[lin] = self._img.linked.audit_self()
                return rvdict

        def ischild(self):
                """Indicates whether the current image is a child image."""
                return self._img.linked.ischild()

        def isparent(self, li_ignore=None):
                """Indicates whether the current image is a parent image."""
                return self._img.linked.isparent(li_ignore)

        @staticmethod
        def __utc_format(time_str, utc_now):
                """Given a local time value string, formatted with
                "%Y-%m-%dT%H:%M:%S, return a UTC representation of that value,
                formatted with %Y%m%dT%H%M%SZ.  This raises a ValueError if the
                time was incorrectly formatted.  If the time_str is "now", it
                returns the value of utc_now"""

                if time_str == "now":
                        return utc_now

                try:
                        local_dt = datetime.datetime.strptime(time_str,
                            "%Y-%m-%dT%H:%M:%S")
                        secs = time.mktime(local_dt.timetuple())
                        utc_dt = datetime.datetime.utcfromtimestamp(secs)
                        return utc_dt.strftime("%Y%m%dT%H%M%SZ")
                except ValueError as e:
                        raise apx.HistoryRequestException(e)

        def __get_history_paths(self, time_val, utc_now):
                """Given a local timestamp, either as a discrete value, or a
                range of values, formatted as '<timestamp>-<timestamp>', and a
                path to find history xml files, return an array of paths that
                match that timestamp.  utc_now is the current time expressed in
                UTC"""

                files = []
                if len(time_val) > 20 or time_val.startswith("now-"):
                        if time_val.startswith("now-"):
                                start = utc_now
                                finish = self.__utc_format(time_val[4:],
                                    utc_now)
                        else:
                                # our ranges are 19 chars of timestamp, a '-',
                                # and another timestamp
                                start = self.__utc_format(time_val[:19],
                                    utc_now)
                                finish = self.__utc_format(time_val[20:],
                                    utc_now)
                        if start > finish:
                                raise apx.HistoryRequestException(_("Start "
                                    "time must be older than finish time: "
                                    "{0}").format(time_val))
                        files = self.__get_history_range(start, finish)
                else:
                        # there can be multiple event files per timestamp
                        prefix = self.__utc_format(time_val, utc_now)
                        files = glob.glob(os.path.join(self._img.history.path,
                            "{0}*".format(prefix)))
                if not files:
                        raise apx.HistoryRequestException(_("No history "
                            "entries found for {0}").format(time_val))
                return files

        def __get_history_range(self, start, finish):
                """Given a start and finish date, formatted as UTC date strings
                as per __utc_format(), return a list of history filenames that
                fall within that date range.  A range of two equal dates is
                the equivalent of just retrieving history for that single date
                string."""

                entries = []
                all_entries = sorted(os.listdir(self._img.history.path))

                for entry in all_entries:
                        # our timestamps are always 16 character datestamps
                        basename = os.path.basename(entry)[:16]
                        if basename >= start:
                                if basename > finish:
                                        # we can stop looking now.
                                        break
                                entries.append(entry)
                return entries

        def gen_history(self, limit=None, times=misc.EmptyI):
                """A generator function that returns History objects up to the
                limit specified matching the times specified.

                'limit' is an optional integer value specifying the maximum
                number of entries to return.

                'times' is a list of timestamp or timestamp range strings to
                restrict the returned entries to."""

                # Make entries a set to cope with multiple overlapping ranges or
                # times.
                entries = set()

                utc_now = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                for time_val in times:
                        # Ranges are 19 chars of timestamp, a '-', and
                        # another timestamp.
                        if len(time_val) > 20 or time_val.startswith("now-"):
                                if time_val.startswith("now-"):
                                        start = utc_now
                                        finish = self.__utc_format(time_val[4:],
                                            utc_now)
                                else:
                                        start = self.__utc_format(time_val[:19],
                                            utc_now)
                                        finish = self.__utc_format(
                                            time_val[20:], utc_now)
                                if start > finish:
                                        raise apx.HistoryRequestException(
                                            _("Start time must be older than "
                                            "finish time: {0}").format(
                                            time_val))
                                files = self.__get_history_range(start, finish)
                        else:
                                # There can be multiple entries per timestamp.
                                prefix = self.__utc_format(time_val, utc_now)
                                files = glob.glob(os.path.join(
                                    self._img.history.path, "{0}*".format(
                                    prefix)))

                        try:
                                files = self.__get_history_paths(time_val,
                                    utc_now)
                                entries.update(files)
                        except ValueError:
                                raise apx.HistoryRequestException(_("Invalid "
                                    "time format '{0}'.  Please use "
                                    "%Y-%m-%dT%H:%M:%S or\n"
                                    "%Y-%m-%dT%H:%M:%S-"
                                    "%Y-%m-%dT%H:%M:%S").format(time_val))

                if not times:
                        try:
                                entries = os.listdir(self._img.history.path)
                        except EnvironmentError as e:
                                if e.errno == errno.ENOENT:
                                        # No history to list.
                                        return
                                raise apx._convert_error(e)

                entries = sorted(entries)
                if limit:
                        limit *= -1
                        entries = entries[limit:]

                try:
                        uuid_be_dic = bootenv.BootEnv.get_uuid_be_dic()
                except apx.ApiException as e:
                        uuid_be_dic = {}

                for entry in entries:
                        # Yield each history entry object as it is loaded.
                        try:
                                yield history.History(
                                    root_dir=self._img.history.root_dir,
                                    filename=entry, uuid_be_dic=uuid_be_dic)
                        except apx.HistoryLoadException as e:
                                if e.parse_failure:
                                        # Ignore corrupt entries.
                                        continue
                                raise

        def get_linked_name(self):
                """If the current image is a child image, this function
                returns a linked image name object which represents the name
                of the current image."""
                return self._img.linked.child_name

        def get_linked_props(self, lin=None):
                """Return a dictionary which represents the linked image
                properties associated with a linked image.

                'lin' is the name of the child image.  If lin is None then
                the current image is assumed to be a linked image and it's
                properties are returned."""

                return self._img.linked.child_props(lin=lin)

        def list_linked(self, li_ignore=None):
                """Returns a list of linked images associated with the
                current image.  This includes both child and parent images.

                For all parameters, refer to the 'gen_plan_install' function
                for an explanation of their usage and effects.

                The returned value is a list of tuples where each tuple
                contains (<li name>, <relationship>, <li path>)."""

                return self._img.linked.list_related(li_ignore=li_ignore)

        def parse_linked_name(self, li_name, allow_unknown=False):
                """Given a string representing a linked image child name,
                returns linked image name object representing the same name.

                'allow_unknown' indicates whether the name must represent
                actual children or simply be syntactically correct."""

                return self._img.linked.parse_name(li_name, allow_unknown)

        def parse_linked_name_list(self, li_name_list, allow_unknown=False):
                """Given a list of strings representing linked image child
                names, returns a list of linked image name objects
                representing the same names.

                For all other parameters, refer to the 'parse_linked_name'
                function for an explanation of their usage and effects."""

                return [
                    self.parse_linked_name(li_name, allow_unknown)
                    for li_name in li_name_list
                ]

        def describe(self):
                """Returns None if no plan is ready yet, otherwise returns
                a PlanDescription."""

                return self.__plan_desc

        def prepare(self):
                """Takes care of things which must be done before the plan can
                be executed.  This includes downloading the packages to disk and
                preparing the indexes to be updated during execution.  Should
                only be called once a gen_plan_*() method has been called.  If
                a plan is abandoned after calling this method, reset() should
                be called."""

                self._acquire_activity_lock()
                try:
                        self._img.lock()
                except:
                        self._activity_lock.release()
                        raise

                try:
                        if not self._img.imageplan:
                                raise apx.PlanMissingException()

                        if not self.__planned_children:
                                # if we never planned children images then we
                                # didn't finish planning.
                                raise apx.PlanMissingException()

                        if self.__prepared:
                                raise apx.AlreadyPreparedException()

                        self._enable_cancel()

                        try:
                                self._img.imageplan.preexecute()
                        except search_errors.ProblematicPermissionsIndexException as e:
                                raise apx.ProblematicPermissionsIndexException(e)
                        except:
                                raise

                        self._disable_cancel()
                        self.__prepared = True
                except apx.CanceledException as e:
                        self._cancel_done()
                        if self._img.history.operation_name:
                                # If an operation is in progress, log
                                # the error and mark its end.
                                self.log_operation_end(error=e)
                        raise
                except Exception as e:
                        self._cancel_cleanup_exception()
                        if self._img.history.operation_name:
                                # If an operation is in progress, log
                                # the error and mark its end.
                                self.log_operation_end(error=e)
                        raise
                except:
                        # Handle exceptions that are not subclasses of
                        # Exception.
                        self._cancel_cleanup_exception()
                        if self._img.history.operation_name:
                                # If an operation is in progress, log
                                # the error and mark its end.
                                exc_type, exc_value, exc_traceback = \
                                    sys.exc_info()
                                self.log_operation_end(error=exc_type)
                        raise
                finally:
                        self._img.cleanup_downloads()
                        self._img.unlock()
                        try:
                                if int(os.environ.get("PKG_DUMP_STATS", 0)) > 0:
                                        self._img.transport.stats.dump()
                        except ValueError:
                                # Don't generate stats if an invalid value
                                # is supplied.
                                pass
                        self._activity_lock.release()

                self._img.linked.api_recurse_prepare(self.__progresstracker)

        def execute_plan(self):
                """Executes the plan. This is uncancelable once it begins.
                Should only be called after the prepare method has been
                called.  After plan execution, reset() should be called."""

                self._acquire_activity_lock()
                try:
                        self._disable_cancel()
                        self._img.lock()
                except:
                        self._activity_lock.release()
                        raise

                try:
                        if not self._img.imageplan:
                                raise apx.PlanMissingException()

                        if not self.__prepared:
                                raise apx.PrematureExecutionException()

                        if self.__executed:
                                raise apx.AlreadyExecutedException()

                        try:
                                be = bootenv.BootEnv(self._img,
                                    self.__progresstracker)
                        except RuntimeError:
                                be = bootenv.BootEnvNull(self._img)
                        self._img.bootenv = be

                        if not self.__new_be and \
                            self._img.imageplan.reboot_needed() and \
                            self._img.is_liveroot():
                                e = apx.RebootNeededOnLiveImageException()
                                self.log_operation_end(error=e)
                                raise e

                        # Before proceeding, create a backup boot environment if
                        # requested.
                        if self.__backup_be:
                                try:
                                        be.create_backup_be(
                                            be_name=self.__backup_be_name)
                                except Exception as e:
                                        self.log_operation_end(error=e)
                                        raise
                                except:
                                        # Handle exceptions that are not
                                        # subclasses of Exception.
                                        exc_type, exc_value, exc_traceback = \
                                            sys.exc_info()
                                        self.log_operation_end(error=exc_type)
                                        raise

                        # After (possibly) creating backup be, determine if
                        # operation should execute on a clone of current BE.
                        if self.__new_be:
                                try:
                                        be.init_image_recovery(self._img,
                                            self.__be_name)
                                except Exception as e:
                                        self.log_operation_end(error=e)
                                        raise
                                except:
                                        # Handle exceptions that are not
                                        # subclasses of Exception.
                                        exc_type, exc_value, exc_traceback = \
                                            sys.exc_info()
                                        self.log_operation_end(error=exc_type)
                                        raise
                                # check if things gained underneath us
                                if self._img.is_liveroot():
                                        e = apx.UnableToCopyBE()
                                        self.log_operation_end(error=e)
                                        raise e

                        raise_later = None

                        # we're about to execute a plan so change our current
                        # working directory to / so that we won't fail if we
                        # try to remove our current working directory
                        os.chdir(os.sep)

                        try:
                                try:
                                        self._img.imageplan.execute()
                                except apx.WrapIndexingException as e:
                                        raise_later = e

                                if not self._img.linked.nothingtodo():
                                        self._img.linked.syncmd()
                        except RuntimeError as e:
                                if self.__new_be:
                                        be.restore_image()
                                else:
                                        be.restore_install_uninstall()
                                # Must be done after bootenv restore.
                                self.log_operation_end(error=e)
                                raise
                        except search_errors.IndexLockedException as e:
                                error = apx.IndexLockedException(e)
                                self.log_operation_end(error=error)
                                raise error
                        except search_errors.ProblematicPermissionsIndexException as e:
                                error = apx.ProblematicPermissionsIndexException(e)
                                self.log_operation_end(error=error)
                                raise error
                        except search_errors.InconsistentIndexException as e:
                                error = apx.CorruptedIndexException(e)
                                self.log_operation_end(error=error)
                                raise error
                        except NonzeroExitException as e:
                                # Won't happen during update
                                be.restore_install_uninstall()
                                error = apx.ActuatorException(e)
                                self.log_operation_end(error=error)
                                raise error

                        except Exception as e:
                                if self.__new_be:
                                        be.restore_image()
                                else:
                                        be.restore_install_uninstall()
                                # Must be done after bootenv restore.
                                self.log_operation_end(error=e)
                                raise
                        except:
                                # Handle exceptions that are not subclasses of
                                # Exception.
                                exc_type, exc_value, exc_traceback = \
                                    sys.exc_info()

                                if self.__new_be:
                                        be.restore_image()
                                else:
                                        be.restore_install_uninstall()
                                # Must be done after bootenv restore.
                                self.log_operation_end(error=exc_type)
                                raise

                        self._img.linked.api_recurse_execute(
                            self.__progresstracker)

                        self.__finished_execution(be)
                        if raise_later:
                                raise raise_later

                finally:
                        self._img.cleanup_downloads()
                        if self._img.locked:
                                self._img.unlock()
                        self._activity_lock.release()

        def __finished_execution(self, be):
                if self._img.imageplan.state != plandesc.EXECUTED_OK:
                        if self.__new_be:
                                be.restore_image()
                        else:
                                be.restore_install_uninstall()

                        error = apx.ImageplanStateException(
                            self._img.imageplan.state)
                        # Must be done after bootenv restore.
                        self.log_operation_end(error=error)
                        raise error

                if self._img.imageplan.boot_archive_needed() or \
                    self.__new_be:
                        be.update_boot_archive()

                if self.__new_be:

                        # Remove any temporary hot-fix source origins from
                        # the cloned BE.
                        for pub in self._img.cfg.publishers.values():
                                if not pub.repository:
                                        continue

                                for o in pub.repository.origins:
                                        if relib.search(
                                            '/pkg_hfa_.*p5p/$', o.uri):
                                                pub.repository.remove_origin(o)
                                                self._img.save_config()

                        be.activate_image(set_active=self.__be_activate)
                else:
                        be.activate_install_uninstall()
                self._img.cleanup_cached_content(
                    progtrack=self.__progresstracker)
                # If the end of the operation wasn't already logged
                # by one of the previous operations, then log it as
                # ending now.
                if self._img.history.operation_name:
                        self.log_operation_end(release_notes=
                            self._img.imageplan.pd.release_notes_name)
                self.__executed = True

        def set_plan_license_status(self, pfmri, plicense, accepted=None,
            displayed=None):
                """Sets the license status for the given package FMRI and
                license entry.

                'accepted' is an optional parameter that can be one of three
                values:
                        None    leaves accepted status unchanged
                        False   sets accepted status to False
                        True    sets accepted status to True

                'displayed' is an optional parameter that can be one of three
                values:
                        None    leaves displayed status unchanged
                        False   sets displayed status to False
                        True    sets displayed status to True"""

                self._acquire_activity_lock()
                try:
                        try:
                                self._disable_cancel()
                        except apx.CanceledException:
                                self._cancel_done()
                                raise

                        if not self._img.imageplan:
                                raise apx.PlanMissingException()

                        for pp in self.__plan_desc.pkg_plans:
                                if pp.destination_fmri == pfmri:
                                        pp.set_license_status(plicense,
                                            accepted=accepted,
                                            displayed=displayed)
                                        break
                finally:
                        self._activity_lock.release()

        def refresh(self, full_refresh=False, pubs=None, immediate=False,
            ignore_unreachable=True):
                """Refreshes the metadata (e.g. catalog) for one or more
                publishers.

                'full_refresh' is an optional boolean value indicating whether
                a full retrieval of publisher metadata (e.g. catalogs) or only
                an update to the existing metadata should be performed.  When
                True, 'immediate' is also set to True.

                'pubs' is a list of publisher prefixes or publisher objects
                to refresh.  Passing an empty list or using the default value
                implies all publishers.

                'immediate' is an optional boolean value indicating whether
                a refresh should occur now.  If False, a publisher's selected
                repository will only be checked for updates if the update
                interval period recorded in the image configuration has been
                exceeded.

                'ignore_unreachable' is an optional boolean value indicating
                whether unreachable repositories should be ignored. If True,
                errors contacting this repository are stored in the transport
                but no exception is raised, allowing an operation to continue
                if an unneeded repository is not online.

                Currently returns an image object, allowing existing code to
                work while the rest of the API is put into place."""

                self._acquire_activity_lock()
                try:
                        self._disable_cancel()
                        self._img.lock()
                        try:
                                self.__refresh(full_refresh=full_refresh,
                                    pubs=pubs,
                                    ignore_unreachable=ignore_unreachable,
                                    immediate=immediate)
                                return self._img
                        finally:
                                self._img.unlock()
                                self._img.cleanup_downloads()
                except apx.CanceledException:
                        self._cancel_done()
                        raise
                finally:
                        try:
                                if int(os.environ.get("PKG_DUMP_STATS", 0)) > 0:
                                        self._img.transport.stats.dump()
                        except ValueError:
                                # Don't generate stats if an invalid value
                                # is supplied.
                                pass
                        self._activity_lock.release()

        def __refresh(self, full_refresh=False, pubs=None, immediate=False,
            ignore_unreachable=True):
                """Private refresh method; caller responsible for locking and
                cleanup."""

                self._img.refresh_publishers(full_refresh=full_refresh,
                    ignore_unreachable=ignore_unreachable,
                    immediate=immediate, pubs=pubs,
                    progtrack=self.__progresstracker)

        def __licenses(self, pfmri, mfst, alt_pub=None):
                """Private function. Returns the license info from the
                manifest mfst."""
                license_lst = []
                for lic in mfst.gen_actions_by_type("license"):
                        license_lst.append(LicenseInfo(pfmri, lic,
                            img=self._img, alt_pub=alt_pub))
                return license_lst

        @_LockedCancelable()
        def get_pkg_categories(self, installed=False, pubs=misc.EmptyI,
            repos=None):
                """Returns an ordered list of tuples of the form (scheme,
                category) containing the names of all categories in use by
                the last version of each unique package in the catalog on a
                per-publisher basis.

                'installed' is an optional boolean value indicating whether
                only the categories used by currently installed packages
                should be returned.  If False, the categories used by the
                latest vesion of every known package will be returned
                instead.

                'pubs' is an optional list of publisher prefixes to restrict
                the results to.

                'repos' is a list of URI strings or RepositoryURI objects that
                represent the locations of package repositories to list packages
                for.
                """

                if installed:
                        excludes = misc.EmptyI
                else:
                        excludes = self._img.list_excludes()

                if repos:
                        ignored, ignored, known_cat, inst_cat = \
                            self.__get_alt_pkg_data(repos)
                        if installed:
                                pkg_cat = inst_cat
                        else:
                                pkg_cat = known_cat
                elif installed:
                        pkg_cat = self._img.get_catalog(
                            self._img.IMG_CATALOG_INSTALLED)
                else:
                        pkg_cat = self._img.get_catalog(
                            self._img.IMG_CATALOG_KNOWN)
                return sorted(pkg_cat.categories(excludes=excludes, pubs=pubs))

        def __map_installed_newest(self, pubs, known_cat=None):
                """Private function.  Maps incorporations and publisher
                relationships for installed packages and returns them
                as a tuple of (pub_ranks, inc_stems, inc_vers, inst_stems,
                ren_stems, ren_inst_stems).
                """

                img_cat = self._img.get_catalog(
                    self._img.IMG_CATALOG_INSTALLED)
                cat_info = frozenset([img_cat.DEPENDENCY])

                inst_stems = {}
                ren_inst_stems = {}
                ren_stems = {}

                inc_stems = {}
                inc_vers = {}

                pub_ranks = self._img.get_publisher_ranks()

                # The incorporation list should include all installed,
                # incorporated packages from all publishers.
                for t in img_cat.entry_actions(cat_info):
                        (pub, stem, ver), entry, actions = t

                        inst_stems[stem] = ver
                        pkgr = False
                        targets = set()
                        try:
                                for a in actions:
                                        if a.name == "set" and \
                                            a.attrs["name"] == "pkg.renamed":
                                                pkgr = True
                                                continue
                                        elif a.name != "depend":
                                                continue

                                        if a.attrs["type"] == "require":
                                                # Because the actions are not
                                                # returned in a guaranteed
                                                # order, the dependencies will
                                                # have to be recorded for
                                                # evaluation later.
                                                targets.add(a.attrs["fmri"])
                                        elif a.attrs["type"] == "incorporate":
                                                # Record incorporated packages.
                                                tgt = fmri.PkgFmri(
                                                    a.attrs["fmri"])
                                                tver = tgt.version
                                                # incorporates without a version
                                                # should be ignored.
                                                if not tver:
                                                        continue
                                                over = inc_vers.get(
                                                    tgt.pkg_name, None)

                                                # In case this package has been
                                                # incorporated more than once,
                                                # use the newest version.
                                                if over is not None and \
                                                    over > tver:
                                                        continue
                                                inc_vers[tgt.pkg_name] = tver
                        except apx.InvalidPackageErrors:
                                # For mapping purposes, ignore unsupported
                                # (and invalid) actions.  This is necessary so
                                # that API consumers can discover new package
                                # data that may be needed to perform an upgrade
                                # so that the API can understand them.
                                pass

                        if pkgr:
                                for f in targets:
                                        tgt = fmri.PkgFmri(f)
                                        ren_stems[tgt.pkg_name] = stem
                                        ren_inst_stems.setdefault(stem,
                                            set())
                                        ren_inst_stems[stem].add(
                                            tgt.pkg_name)

                def check_stem(t, entry):
                        pub, stem, ver = t
                        if stem in inst_stems:
                                iver = inst_stems[stem]
                                if stem in ren_inst_stems or \
                                    ver == iver:
                                        # The package has been renamed
                                        # or the entry is for the same
                                        # version as that which is
                                        # installed, so doesn't need
                                        # to be checked.
                                        return False
                                # The package may have been renamed in
                                # a newer version, so must be checked.
                                return True
                        elif stem in inc_vers:
                                # Package is incorporated, but not
                                # installed, so should be checked.
                                return True

                        tgt = ren_stems.get(stem, None)
                        while tgt is not None:
                                # This seems counter-intuitive, but
                                # for performance and other reasons,
                                # this stem should only be checked
                                # for a rename if it is incorporated
                                # or installed using a previous name.
                                if tgt in inst_stems or \
                                    tgt in inc_vers:
                                        return True
                                tgt = ren_stems.get(tgt, None)

                        # Package should not be checked.
                        return False

                if not known_cat:
                        known_cat = self._img.get_catalog(
                            self._img.IMG_CATALOG_KNOWN)

                # Find terminal rename entry for all known packages not
                # rejected by check_stem().
                for t, entry, actions in known_cat.entry_actions(cat_info,
                    cb=check_stem, last=True):
                        pkgr = False
                        targets = set()
                        try:
                                for a in actions:
                                        if a.name == "set" and \
                                            a.attrs["name"] == "pkg.renamed":
                                                pkgr = True
                                                continue

                                        if a.name != "depend":
                                                continue

                                        if a.attrs["type"] != "require":
                                                continue

                                        # Because the actions are not
                                        # returned in a guaranteed
                                        # order, the dependencies will
                                        # have to be recorded for
                                        # evaluation later.
                                        targets.add(a.attrs["fmri"])
                        except apx.InvalidPackageErrors:
                                # For mapping purposes, ignore unsupported
                                # (and invalid) actions.  This is necessary so
                                # that API consumers can discover new package
                                # data that may be needed to perform an upgrade
                                # so that the API can understand them.
                                pass

                        if pkgr:
                                pub, stem, ver = t
                                for f in targets:
                                        tgt = fmri.PkgFmri(f)
                                        ren_stems[tgt.pkg_name] = stem

                # Determine highest ranked publisher for package stems
                # listed in installed incorporations.
                def pub_key(item):
                        return pub_ranks[item][0]

                for p in sorted(pub_ranks, key=pub_key):
                        if pubs and p not in pubs:
                                continue
                        for stem in known_cat.names(pubs=[p]):
                                if stem in inc_vers:
                                        inc_stems.setdefault(stem, p)

                return (pub_ranks, inc_stems, inc_vers, inst_stems, ren_stems,
                    ren_inst_stems)

        def __get_temp_repo_pubs(self, repos):
                """Private helper function to retrieve publisher information
                from list of temporary repositories.  Caller is responsible
                for locking."""

                ret_pubs = []
                for repo_uri in repos:
                        if isinstance(repo_uri, six.string_types):
                                repo = publisher.RepositoryURI(repo_uri)
                        else:
                                # Already a RepositoryURI.
                                repo = repo_uri

                        pubs = None
                        try:
                                pubs = self._img.transport.get_publisherdata(
                                    repo, ccancel=self.__check_cancel)
                        except apx.UnsupportedRepositoryOperation:
                                raise apx.RepoPubConfigUnavailable(
                                    location=str(repo))

                        if not pubs:
                                # Empty repository configuration.
                                raise apx.RepoPubConfigUnavailable(
                                    location=str(repo))

                        for p in pubs:
                                p.client_uuid = "transient"
                                psrepo = p.repository
                                if not psrepo:
                                        # Repository configuration info wasn't
                                        # provided, so assume origin is
                                        # repo_uri.
                                        p.repository = publisher.Repository(
                                            origins=[repo_uri])
                                elif not psrepo.origins:
                                        # Repository configuration was provided,
                                        # but without an origin.  Assume the
                                        # repo_uri is the origin.
                                        psrepo.add_origin(repo_uri)
                                elif repo not in psrepo.origins:
                                        # If the repo_uri used is not
                                        # in the list of sources, then
                                        # add it as the first origin.
                                        psrepo.origins.insert(0, repo)
                        ret_pubs.extend(pubs)

                return sorted(ret_pubs)

        def __get_alt_pkg_data(self, repos):
                """Private helper function to retrieve composite known and
                installed catalog and package repository map for temporary
                set of package repositories.  Returns (pkg_pub_map, alt_pubs,
                known_cat, inst_cat)."""

                repos = set(repos)
                eid = ",".join(sorted(map(str, repos)))
                try:
                        return self.__alt_sources[eid]
                except KeyError:
                        # Need to cache new set of alternate sources.
                        pass

                img_inst_cat = self._img.get_catalog(
                    self._img.IMG_CATALOG_INSTALLED)
                img_inst_base = img_inst_cat.get_part("catalog.base.C",
                    must_exist=True)
                op_time = datetime.datetime.utcnow()
                pubs = self.__get_temp_repo_pubs(repos)
                progtrack = self.__progresstracker

                # Create temporary directories.
                tmpdir = tempfile.mkdtemp()

                pkg_repos = {}
                pkg_pub_map = {}
                # Too many nested blocks;
                # pylint: disable=R0101
                try:
                        progtrack.refresh_start(len(pubs), full_refresh=False)
                        failed = []
                        pub_cats = []
                        for pub in pubs:
                                # Assign a temporary meta root to each
                                # publisher.
                                meta_root = os.path.join(tmpdir, str(id(pub)))
                                misc.makedirs(meta_root)
                                pub.meta_root = meta_root
                                pub.transport = self._img.transport
                                repo = pub.repository
                                pkg_repos[id(repo)] = repo

                                # Retrieve each publisher's catalog.
                                progtrack.refresh_start_pub(pub)
                                try:
                                        pub.refresh()
                                except apx.PermissionsException as e:
                                        failed.append((pub, e))
                                        # No point in continuing since no data
                                        # can be written.
                                        break
                                except apx.ApiException as e:
                                        failed.append((pub, e))
                                        continue
                                finally:
                                        progtrack.refresh_end_pub(pub)
                                pub_cats.append((
                                    pub.prefix,
                                    repo,
                                    pub.catalog
                                ))

                        progtrack.refresh_done()

                        if failed:
                                total = len(pub_cats) + len(failed)
                                e = apx.CatalogRefreshException(failed, total,
                                    len(pub_cats))
                                raise e

                        # Determine upgradability.
                        newest = {}
                        for pfx, repo, cat in [(None, None, img_inst_cat)] + \
                            pub_cats:
                                if pfx:
                                        pkg_list = cat.fmris(last=True,
                                            pubs=[pfx])
                                else:
                                        pkg_list = cat.fmris(last=True)

                                for f in pkg_list:
                                        nver, snver = newest.get(f.pkg_name,
                                            (None, None))
                                        if f.version > nver:
                                                newest[f.pkg_name] = (f.version,
                                                    str(f.version))

                        # Build list of installed packages.
                        inst_stems = {}
                        for t, entry in img_inst_cat.tuple_entries():
                                states = entry["metadata"]["states"]
                                if pkgdefs.PKG_STATE_INSTALLED not in states:
                                        continue
                                pub, stem, ver = t
                                inst_stems.setdefault(pub, {})
                                inst_stems[pub].setdefault(stem, {})
                                inst_stems[pub][stem][ver] = False

                        # Now create composite known and installed catalogs.
                        compicat = pkg.catalog.Catalog(batch_mode=True,
                            sign=False)
                        compkcat = pkg.catalog.Catalog(batch_mode=True,
                            sign=False)

                        sparts = (
                           (pfx, cat, repo, name, cat.get_part(name, must_exist=True))
                           for pfx, repo, cat in pub_cats
                           for name in cat.parts
                        )

                        excludes = self._img.list_excludes()
                        proc_stems = {}
                        for pfx, cat, repo, name, spart in sparts:
                                # 'spart' is the source part.
                                if spart is None:
                                        # Client hasn't retrieved this part.
                                        continue

                                # New known part.
                                nkpart = compkcat.get_part(name)
                                nipart = compicat.get_part(name)
                                base = name.startswith("catalog.base.")

                                # Avoid accessor overhead since these will be
                                # used for every entry.
                                cat_ver = cat.version
                                dp = cat.get_part("catalog.dependency.C",
                                    must_exist=True)

                                for t, sentry in spart.tuple_entries(pubs=[pfx]):
                                        pub, stem, ver = t

                                        pkg_pub_map.setdefault(pub, {})
                                        pkg_pub_map[pub].setdefault(stem, {})
                                        pkg_pub_map[pub][stem].setdefault(ver,
                                            set())
                                        pkg_pub_map[pub][stem][ver].add(
                                            id(repo))

                                        if pub in proc_stems and \
                                            stem in proc_stems[pub] and \
                                            ver in proc_stems[pub][stem]:
                                                if id(cat) != proc_stems[pub][stem][ver]:
                                                        # Already added from another
                                                        # catalog.
                                                        continue
                                        else:
                                                proc_stems.setdefault(pub, {})
                                                proc_stems[pub].setdefault(stem,
                                                    {})
                                                proc_stems[pub][stem][ver] = \
                                                    id(cat)

                                        installed = False
                                        if pub in inst_stems and \
                                            stem in inst_stems[pub] and \
                                            ver in inst_stems[pub][stem]:
                                                installed = True
                                                inst_stems[pub][stem][ver] = \
                                                    True

                                        # copy() is too slow here and catalog
                                        # entries are shallow so this should be
                                        # sufficient.
                                        entry = dict(six.iteritems(sentry))
                                        if not base:
                                                # Nothing else to do except add
                                                # the entry for non-base catalog
                                                # parts.
                                                nkpart.add(metadata=entry,
                                                    op_time=op_time, pub=pub,
                                                    stem=stem, ver=ver)
                                                if installed:
                                                        nipart.add(
                                                            metadata=entry,
                                                            op_time=op_time,
                                                            pub=pub, stem=stem,
                                                            ver=ver)
                                                continue

                                        # Only the base catalog part stores
                                        # package state information and/or
                                        # other metadata.
                                        mdata = {}
                                        if installed:
                                                mdata = dict(
                                                    img_inst_base.get_entry(
                                                    pub=pub, stem=stem,
                                                    ver=ver)["metadata"])

                                        entry["metadata"] = mdata

                                        states = [pkgdefs.PKG_STATE_KNOWN,
                                            pkgdefs.PKG_STATE_ALT_SOURCE]
                                        if cat_ver == 0:
                                                states.append(
                                                    pkgdefs.PKG_STATE_V0)
                                        else:
                                                # Assume V1 catalog source.
                                                states.append(
                                                    pkgdefs.PKG_STATE_V1)

                                        if installed:
                                                states.append(
                                                    pkgdefs.PKG_STATE_INSTALLED)

                                        nver, snver = newest.get(stem,
                                            (None, None))
                                        if snver is not None and ver != snver:
                                                states.append(
                                                    pkgdefs.PKG_STATE_UPGRADABLE)

                                        # Determine if package is obsolete or
                                        # has been renamed and mark with
                                        # appropriate state.
                                        dpent = None
                                        if dp is not None:
                                                dpent = dp.get_entry(pub=pub,
                                                    stem=stem, ver=ver)
                                        if dpent is not None:
                                                for a in dpent["actions"]:
                                                        # Constructing action
                                                        # objects for every
                                                        # action would be a lot
                                                        # slower, so a simple
                                                        # string match is done
                                                        # first so that only
                                                        # interesting actions
                                                        # get constructed.
                                                        if not a.startswith("set"):
                                                                continue
                                                        if not ("pkg.obsolete" in a or \
                                                            "pkg.renamed" in a):
                                                                continue

                                                        try:
                                                                act = pkg.actions.fromstr(a)
                                                        except pkg.actions.ActionError:
                                                                # If the action can't be
                                                                # parsed or is not yet
                                                                # supported, continue.
                                                                continue

                                                        if act.attrs["value"].lower() != "true":
                                                                continue

                                                        if act.attrs["name"] == "pkg.obsolete":
                                                                states.append(
                                                                    pkgdefs.PKG_STATE_OBSOLETE)
                                                        elif act.attrs["name"] == "pkg.renamed":
                                                                if not act.include_this(
                                                                    excludes, publisher=pub):
                                                                        continue
                                                                states.append(
                                                                    pkgdefs.PKG_STATE_RENAMED)

                                        mdata["states"] = states

                                        # Add base entries.
                                        nkpart.add(metadata=entry,
                                            op_time=op_time, pub=pub, stem=stem,
                                            ver=ver)
                                        if installed:
                                                nipart.add(metadata=entry,
                                                    op_time=op_time, pub=pub,
                                                    stem=stem, ver=ver)

                        pub_map = {}
                        for pub in pubs:
                                try:
                                        opub = pub_map[pub.prefix]
                                except KeyError:
                                        nrepo = publisher.Repository()
                                        opub = publisher.Publisher(pub.prefix,
                                            catalog=compkcat, repository=nrepo)
                                        pub_map[pub.prefix] = opub

                        rid_map = {}
                        for pub in pkg_pub_map:
                                for stem in pkg_pub_map[pub]:
                                        for ver in pkg_pub_map[pub][stem]:
                                                rids = tuple(sorted(
                                                    pkg_pub_map[pub][stem][ver]))

                                                if rids not in rid_map:
                                                        # Create a publisher and
                                                        # repository for this
                                                        # unique set of origins.
                                                        origins = []
                                                        list(map(origins.extend, [
                                                           pkg_repos.get(rid).origins
                                                           for rid in rids
                                                        ]))
                                                        npub = \
                                                            copy.copy(pub_map[pub])
                                                        nrepo = npub.repository
                                                        nrepo.origins = origins
                                                        assert npub.catalog == \
                                                            compkcat
                                                        rid_map[rids] = npub

                                                pkg_pub_map[pub][stem][ver] = \
                                                    rid_map[rids]

                        # Now consolidate all origins for each publisher under
                        # a single repository object for the caller.
                        for pub in pubs:
                                npub = pub_map[pub.prefix]
                                nrepo = npub.repository
                                for o in pub.repository.origins:
                                        if not nrepo.has_origin(o):
                                                nrepo.add_origin(o)
                                assert npub.catalog == compkcat

                        for compcat in (compicat, compkcat):
                                compcat.batch_mode = False
                                compcat.finalize()
                                compcat.read_only = True

                        # Cache these for future callers.
                        self.__alt_sources[eid] = (pkg_pub_map,
                            sorted(pub_map.values()), compkcat, compicat)
                        return self.__alt_sources[eid]
                finally:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                        self._img.cleanup_downloads()

        @_LockedGenerator()
        def get_pkg_list(self, pkg_list, cats=None, collect_attrs=False,
            patterns=misc.EmptyI, pubs=misc.EmptyI, raise_unmatched=False,
            ranked=False, repos=None, return_fmris=False, variants=False):
                """A generator function that produces tuples of the form:

                    (
                        (
                            pub,    - (string) the publisher of the package
                            stem,   - (string) the name of the package
                            version - (string) the version of the package
                        ),
                        summary,    - (string) the package summary
                        categories, - (list) string tuples of (scheme, category)
                        states,     - (list) PackageInfo states
                        attributes  - (dict) package attributes
                    )

                Results are always sorted by stem, publisher, and then in
                descending version order.

                'pkg_list' is one of the following constant values indicating
                what base set of package data should be used for results:

                        LIST_ALL
                                All known packages.

                        LIST_INSTALLED
                                Installed packages.

                        LIST_INSTALLED_NEWEST
                                Installed packages and the newest
                                versions of packages not installed.
                                Renamed packages that are listed in
                                an installed incorporation will be
                                excluded unless they are installed.

                        LIST_NEWEST
                                The newest versions of all known packages
                                that match the provided patterns and
                                other criteria.

                        LIST_UPGRADABLE
                                Packages that are installed and upgradable.

                'cats' is an optional list of package category tuples of the
                form (scheme, cat) to restrict the results to.  If a package
                is assigned to any of the given categories, it will be
                returned.  A value of [] will return packages not assigned
                to any package category.  A value of None indicates that no
                package category filtering should be applied.

                'collect_attrs' is an optional boolean that indicates whether
                all package attributes should be collected and returned in the
                fifth element of the return tuple.  If False, that element will
                be an empty dictionary.

                'patterns' is an optional list of FMRI wildcard strings to
                filter results by.

                'pubs' is an optional list of publisher prefixes to restrict
                the results to.

                'raise_unmatched' is an optional boolean value that indicates
                whether an InventoryException should be raised if any patterns
                (after applying all other filtering and returning all results)
                didn't match any packages.

                'ranked' is an optional boolean value that indicates whether
                only the matching package versions from the highest-ranked
                publisher should be returned.  This option is ignored for
                patterns that explicitly specify the publisher to match.

                'repos' is a list of URI strings or RepositoryURI objects that
                represent the locations of package repositories to list packages
                for.

                'return_fmris' is an optional boolean value that indicates that
                an FMRI object should be returned in place of the (pub, stem,
                ver) tuple that is normally returned.

                'variants' is an optional boolean value that indicates that
                packages that are for arch or zone variants not applicable to
                this image should be returned.

                Please note that this function may invoke network operations
                to retrieve the requested package information."""

                return self.__get_pkg_list(pkg_list, cats=cats,
                    collect_attrs=collect_attrs, patterns=patterns, pubs=pubs,
                    raise_unmatched=raise_unmatched, ranked=ranked, repos=repos,
                    return_fmris=return_fmris, variants=variants)

        def __get_pkg_list(self, pkg_list, cats=None, collect_attrs=False,
            inst_cat=None, known_cat=None, patterns=misc.EmptyI,
            pubs=misc.EmptyI, raise_unmatched=False, ranked=False, repos=None,
            return_fmris=False, return_metadata=False, variants=False):
                """This is the implementation of get_pkg_list.  The other
                function is a wrapper that uses locking.  The separation was
                necessary because of API functions that already perform locking
                but need to use get_pkg_list().  This is a generator
                function."""

                installed = inst_newest = newest = upgradable = False
                if pkg_list == self.LIST_INSTALLED:
                        installed = True
                elif pkg_list == self.LIST_INSTALLED_NEWEST:
                        inst_newest = True
                elif pkg_list == self.LIST_NEWEST:
                        newest = True
                elif pkg_list == self.LIST_UPGRADABLE:
                        upgradable = True

                # Each pattern in patterns can be a partial or full FMRI, so
                # extract the individual components for use in filtering.
                illegals = []
                pat_tuples = {}
                pat_versioned = False
                latest_pats = set()
                seen = set()
                npatterns = set()
                for pat, error, pfmri, matcher in self.parse_fmri_patterns(
                    patterns):
                        if error:
                                illegals.append(error)
                                continue

                        # Duplicate patterns are ignored.
                        sfmri = str(pfmri)
                        if sfmri in seen:
                                # A different form of the same pattern
                                # was specified already; ignore this
                                # one (e.g. pkg:/network/ping,
                                # /network/ping).
                                continue

                        # Track used patterns.
                        seen.add(sfmri)
                        npatterns.add(pat)

                        if "@" in pat:
                                # Mark that a pattern contained version
                                # information.  This is used for a listing
                                # optimization later on.
                                pat_versioned = True
                        if getattr(pfmri.version, "match_latest", None):
                                latest_pats.add(pat)
                        pat_tuples[pat] = (pfmri.tuple(), matcher)

                patterns = npatterns
                del npatterns, seen

                if illegals:
                        raise apx.InventoryException(illegal=illegals)

                if repos:
                        ignored, ignored, known_cat, inst_cat = \
                            self.__get_alt_pkg_data(repos)

                # For LIST_INSTALLED_NEWEST, installed packages need to be
                # determined and incorporation and publisher relationships
                # mapped.
                if inst_newest:
                        pub_ranks, inc_stems, inc_vers, inst_stems, ren_stems, \
                            ren_inst_stems = self.__map_installed_newest(
                            pubs, known_cat=known_cat)
                else:
                        pub_ranks = inc_stems = inc_vers = inst_stems = \
                            ren_stems = ren_inst_stems = misc.EmptyDict

                if installed or upgradable:
                        if inst_cat:
                                pkg_cat = inst_cat
                        else:
                                pkg_cat = self._img.get_catalog(
                                    self._img.IMG_CATALOG_INSTALLED)

                        # Don't need to perform variant filtering if only
                        # listing installed packages.
                        variants = True
                elif known_cat:
                        pkg_cat = known_cat
                else:
                        pkg_cat = self._img.get_catalog(
                            self._img.IMG_CATALOG_KNOWN)

                cat_info = frozenset([pkg_cat.DEPENDENCY, pkg_cat.SUMMARY])

                # Keep track of when the newest version has been found for
                # each incorporated stem.
                slist = set()

                # Keep track of listed stems for all other packages on a
                # per-publisher basis.
                nlist = collections.defaultdict(int)

                def check_state(t, entry):
                        states = entry["metadata"]["states"]
                        pkgi = pkgdefs.PKG_STATE_INSTALLED in states
                        pkgu = pkgdefs.PKG_STATE_UPGRADABLE in states
                        pub, stem, ver = t

                        if upgradable:
                                # If package is marked upgradable, return it.
                                return pkgu
                        elif pkgi:
                                # Nothing more to do here.
                                return True
                        elif stem in inst_stems:
                                # Some other version of this package is
                                # installed, so this one should not be
                                # returned.
                                return False

                        # Attempt to determine if this package is installed
                        # under a different name or constrained under a
                        # different name.
                        tgt = ren_stems.get(stem, None)
                        while tgt is not None:
                                if tgt in inc_vers:
                                        # Package is incorporated under a
                                        # different name, so allow this
                                        # to fallthrough to the incoporation
                                        # evaluation.
                                        break
                                elif tgt in inst_stems:
                                        # Package is installed under a
                                        # different name, so skip it.
                                        return False
                                tgt = ren_stems.get(tgt, None)

                        # Attempt to find a suitable version to return.
                        if stem in inc_vers:
                                # For package stems that are incorporated, only
                                # return the newest successor version  based on
                                # publisher rank.
                                if stem in slist:
                                        # Newest version already returned.
                                        return False

                                if stem in inc_stems and \
                                    pub != inc_stems[stem]:
                                        # This entry is for a lower-ranked
                                        # publisher.
                                        return False

                                # XXX version should not require build release.
                                ever = pkg.version.Version(ver)

                                # If the entry's version is a successor to
                                # the incorporated version, then this is the
                                # 'newest' version of this package since
                                # entries are processed in descending version
                                # order.
                                iver = inc_vers[stem]
                                if ever.is_successor(iver,
                                    pkg.version.CONSTRAINT_AUTO):
                                        slist.add(stem)
                                        return True
                                return False

                        pkg_stem = "!".join((pub, stem))
                        if pkg_stem in nlist:
                                # A newer version has already been listed for
                                # this stem and publisher.
                                return False
                        return True

                filter_cb = None
                if inst_newest or upgradable:
                        # Filtering needs to be applied.
                        filter_cb = check_state

                excludes = self._img.list_excludes()
                img_variants = self._img.get_variants()

                matched_pats = set()
                pkg_matching_pats = None

                # Retrieve only the newest package versions for LIST_NEWEST if
                # none of the patterns have version information and variants are
                # included.  (This cuts down on the number of entries that have
                # to be filtered.)
                use_last = newest and not pat_versioned and variants

                if ranked:
                        # If caller requested results to be ranked by publisher,
                        # then the list of publishers to return must be passed
                        # to entry_actions() in rank order.
                        pub_ranks = self._img.get_publisher_ranks()
                        if not pubs:
                                # It's important that the list of possible
                                # publishers is gleaned from the catalog
                                # directly and not image configuration so
                                # that temporary sources (archives, etc.)
                                # work as expected.
                                pubs = pkg_cat.publishers()
                        for p in pubs:
                                pub_ranks.setdefault(p, (99, (p, False, False)))

                        def pub_key(a):
                                return (pub_ranks[a], a)

                        pubs = sorted(pubs, key=pub_key)

                # Too many nested blocks;
                # pylint: disable=R0101
                ranked_stems = {}
                for t, entry, actions in pkg_cat.entry_actions(cat_info,
                    cb=filter_cb, excludes=excludes, last=use_last,
                    ordered=True, pubs=pubs):
                        pub, stem, ver = t

                        omit_ver = False
                        omit_package = None

                        pkg_stem = "!".join((pub, stem))
                        if newest and pkg_stem in nlist:
                                # A newer version has already been listed, so
                                # any additional entries need to be marked for
                                # omission before continuing.
                                omit_package = True
                        elif ranked and not patterns and \
                            ranked_stems.get(stem, pub) != pub:
                                # A different version from a higher-ranked
                                # publisher has been returned already, so skip
                                # this one.  This can only be done safely at
                                # this point if no patterns have been specified,
                                # since publisher-specific patterns override
                                # ranking behaviour.
                                omit_package = True
                        else:
                                nlist[pkg_stem] += 1

                        if raise_unmatched:
                                pkg_matching_pats = set()
                        if not omit_package:
                                ever = None
                                for pat in patterns:
                                        (pat_pub, pat_stem, pat_ver), matcher = \
                                            pat_tuples[pat]

                                        if pat_pub is not None and \
                                            pub != pat_pub:
                                                # Publisher doesn't match.
                                                if omit_package is None:
                                                        omit_package = True
                                                continue
                                        elif ranked and not pat_pub and \
                                            ranked_stems.get(stem, pub) != pub:
                                                # A different version from a
                                                # higher-ranked publisher has
                                                # been returned already, so skip
                                                # this one since no publisher
                                                # was specified for the pattern.
                                                if omit_package is None:
                                                        omit_package = True
                                                continue

                                        if matcher == self.MATCH_EXACT:
                                                if pat_stem != stem:
                                                        # Stem doesn't match.
                                                        if omit_package is None:
                                                                omit_package = \
                                                                    True
                                                        continue
                                        elif matcher == self.MATCH_FMRI:
                                                if not ("/" + stem).endswith(
                                                    "/" + pat_stem):
                                                        # Stem doesn't match.
                                                        if omit_package is None:
                                                                omit_package = \
                                                                    True
                                                        continue
                                        elif matcher == self.MATCH_GLOB:
                                                if not fnmatch.fnmatchcase(stem,
                                                    pat_stem):
                                                        # Stem doesn't match.
                                                        if omit_package is None:
                                                                omit_package = \
                                                                    True
                                                        continue

                                        if pat_ver is not None:
                                                if ever is None:
                                                        # Avoid constructing a
                                                        # version object more
                                                        # than once for each
                                                        # entry.
                                                        ever = pkg.version.Version(ver)
                                                if not ever.is_successor(pat_ver,
                                                    pkg.version.CONSTRAINT_AUTO):
                                                        if omit_package is None:
                                                                omit_package = \
                                                                    True
                                                        omit_ver = True
                                                        continue

                                        if pat in latest_pats and \
                                            nlist[pkg_stem] > 1:
                                                # Package allowed by pattern,
                                                # but isn't the "latest"
                                                # version.
                                                if omit_package is None:
                                                        omit_package = True
                                                omit_ver = True
                                                continue

                                        # If this entry matched at least one
                                        # pattern, then ensure it is returned.
                                        omit_package = False
                                        if not raise_unmatched:
                                                # It's faster to stop as soon
                                                # as a match is found.
                                                break

                                        # If caller has requested other match
                                        # cases be raised as an exception, then
                                        # all patterns must be tested for every
                                        # entry.  This is slower, so only done
                                        # if necessary.
                                        pkg_matching_pats.add(pat)

                        if omit_package:
                                # Package didn't match critera; skip it.
                                if (filter_cb is not None or (newest and
                                    pat_versioned)) and omit_ver and \
                                    nlist[pkg_stem] == 1:
                                        # If omitting because of version, and
                                        # no other versions have been returned
                                        # yet for this stem, then discard
                                        # tracking entry so that other
                                        # versions will be listed.
                                        del nlist[pkg_stem]
                                        slist.discard(stem)
                                continue

                        # Perform image arch and zone variant filtering so
                        # that only packages appropriate for this image are
                        # returned, but only do this for packages that are
                        # not installed.
                        pcats = []
                        pkgr = False
                        unsupported = False
                        summ = None
                        targets = set()

                        omit_var = False
                        mdata = entry["metadata"]
                        states = mdata["states"]
                        pkgi = pkgdefs.PKG_STATE_INSTALLED in states
                        ddm = lambda: collections.defaultdict(list)
                        attrs = collections.defaultdict(ddm)
                        try:
                                for a in actions:
                                        if a.name == "depend" and \
                                            a.attrs["type"] == "require":
                                                targets.add(a.attrs["fmri"])
                                                continue
                                        if a.name != "set":
                                                continue

                                        atname = a.attrs["name"]
                                        atvalue = a.attrs["value"]
                                        if collect_attrs:
                                                atvlist = a.attrlist("value")

                                                # XXX Need to describe this data
                                                # structure sanely somewhere.
                                                mods = tuple(
                                                    (k, tuple(sorted(a.attrlist(k))))
                                                    for k in sorted(six.iterkeys(a.attrs))
                                                    if k not in ("name", "value")
                                                )
                                                attrs[atname][mods].extend(atvlist)

                                        if atname == "pkg.summary":
                                                summ = atvalue
                                                continue

                                        if atname == "description":
                                                if summ is None:
                                                        # Historical summary
                                                        # field.
                                                        summ = atvalue
                                                        # pylint: disable=W0106
                                                        collect_attrs and \
                                                            attrs["pkg.summary"] \
                                                            [mods]. \
                                                            extend(atvlist)
                                                continue

                                        if atname == "info.classification":
                                                pcats.extend(
                                                    a.parse_category_info())

                                        if pkgi:
                                                # No filtering for installed
                                                # packages.
                                                continue

                                        # Rename filtering should only be
                                        # performed for incorporated packages
                                        # at this point.
                                        if atname == "pkg.renamed":
                                                if stem in inc_vers:
                                                        pkgr = True
                                                continue

                                        if variants or \
                                            not atname.startswith("variant."):
                                                # No variant filtering required.
                                                continue

                                        # For all variants explicitly set in the
                                        # image, elide packages that are not for
                                        # a matching variant value.
                                        is_list = type(atvalue) == list
                                        for vn, vv in six.iteritems(img_variants):
                                                if vn == atname and \
                                                    ((is_list and
                                                    vv not in atvalue) or \
                                                    (not is_list and
                                                    vv != atvalue)):
                                                        omit_package = True
                                                        omit_var = True
                                                        break
                        except apx.InvalidPackageErrors:
                                # Ignore errors for packages that have invalid
                                # or unsupported metadata.  This is necessary so
                                # that API consumers can discover new package
                                # data that may be needed to perform an upgrade
                                # so that the API can understand them.
                                states = set(states)
                                states.add(PackageInfo.UNSUPPORTED)
                                unsupported = True

                        if not pkgi and pkgr and stem in inc_vers:
                                # If the package is not installed, but this is
                                # the terminal version entry for the stem and
                                # it is an incorporated package, then omit the
                                # package if it has been installed or is
                                # incorporated using one of the new names.
                                for e in targets:
                                        tgt = e
                                        while tgt is not None:
                                                if tgt in ren_inst_stems or \
                                                    tgt in inc_vers:
                                                        omit_package = True
                                                        break
                                                tgt = ren_stems.get(tgt, None)

                        if omit_package:
                                # Package didn't match criteria; skip it.
                                if (filter_cb is not None or newest) and \
                                    omit_var and nlist[pkg_stem] == 1:
                                        # If omitting because of variant, and
                                        # no other versions have been returned
                                        # yet for this stem, then discard
                                        # tracking entry so that other
                                        # versions will be listed.
                                        del nlist[pkg_stem]
                                        slist.discard(stem)
                                continue

                        if cats is not None:
                                if not cats:
                                        if pcats:
                                                # Only want packages with no
                                                # categories.
                                                continue
                                elif not [sc for sc in cats if sc in pcats]:
                                        # Package doesn't match specified
                                        # category criteria.
                                        continue

                        # Return the requested package data.
                        if not unsupported:
                                # Prevent modification of state data.
                                states = frozenset(states)

                        if raise_unmatched:
                                # Only after all other filtering has been
                                # applied are the patterns that the package
                                # matched considered "matching".
                                matched_pats.update(pkg_matching_pats)
                        if ranked:
                                # Only after all other filtering has been
                                # applied is the stem considered to have been
                                # a "ranked" match.
                                ranked_stems.setdefault(stem, pub)

                        if return_fmris:
                                pfmri = fmri.PkgFmri(name=stem, publisher=pub,
                                        version=ver)
                                if return_metadata:
                                        yield (pfmri, summ, pcats, states,
                                            attrs, mdata)
                                else:
                                        yield (pfmri, summ, pcats, states,
                                            attrs)
                        else:
                                if return_metadata:
                                        yield (t, summ, pcats, states,
                                            attrs, mdata)
                                else:
                                        yield (t, summ, pcats, states,
                                            attrs)

                if raise_unmatched:
                        # Caller has requested that non-matching patterns or
                        # patterns that match multiple packages cause an
                        # exception to be raised.
                        notfound = set(pat_tuples.keys()) - matched_pats
                        if raise_unmatched and notfound:
                                raise apx.InventoryException(notfound=notfound)

        @_LockedCancelable()
        def info(self, fmri_strings, local, info_needed, ranked=False,
            repos=None):
                """Gathers information about fmris.  fmri_strings is a list
                of fmri_names for which information is desired.  local
                determines whether to retrieve the information locally
                (if possible).  It returns a dictionary of lists.  The keys
                for the dictionary are the constants specified in the class
                definition.  The values are lists of PackageInfo objects or
                strings.

                'ranked' is an optional boolean value that indicates whether
                only the matching package versions from the highest-ranked
                publisher should be returned.  This option is ignored for
                patterns that explicitly specify the publisher to match.

                'repos' is a list of URI strings or RepositoryURI objects that
                represent the locations of packages to return information for.
                """

                bad_opts = info_needed - PackageInfo.ALL_OPTIONS
                if bad_opts:
                        raise apx.UnrecognizedOptionsToInfo(bad_opts)

                self.log_operation_start("info")

                # Common logic for image and temp repos case.
                if local:
                        ilist = self.LIST_INSTALLED
                else:
                        # Verify validity of certificates before attempting
                        # network operations.
                        self.__cert_verify(log_op_end=[apx.CertificateError])
                        ilist = self.LIST_NEWEST

                # The pkg_pub_map is only populated when temp repos are
                # specified and maps packages to the repositories that
                # contain them for manifest retrieval.
                pkg_pub_map = None
                known_cat = None
                inst_cat = None
                if repos:
                        pkg_pub_map, ignored, known_cat, inst_cat = \
                            self.__get_alt_pkg_data(repos)
                        if local:
                                pkg_cat = inst_cat
                        else:
                                pkg_cat = known_cat
                elif local:
                        pkg_cat = self._img.get_catalog(
                            self._img.IMG_CATALOG_INSTALLED)
                        if not fmri_strings and pkg_cat.package_count == 0:
                                self.log_operation_end(
                                    result=RESULT_NOTHING_TO_DO)
                                raise apx.NoPackagesInstalledException()
                else:
                        pkg_cat = self._img.get_catalog(
                            self._img.IMG_CATALOG_KNOWN)

                excludes = self._img.list_excludes()

                # Set of options that can use catalog data.
                cat_opts = frozenset([PackageInfo.DESCRIPTION,
                    PackageInfo.DEPENDENCIES])

                # Set of options that require manifest retrieval.
                act_opts = PackageInfo.ACTION_OPTIONS - \
                    frozenset([PackageInfo.DEPENDENCIES])

                collect_attrs = PackageInfo.ALL_ATTRIBUTES in info_needed

                pis = []
                rval = {
                    self.INFO_FOUND: pis,
                    self.INFO_MISSING: misc.EmptyI,
                    self.INFO_ILLEGALS: misc.EmptyI,
                }

                try:
                        for pfmri, summary, cats, states, attrs, mdata in \
                            self.__get_pkg_list(
                            ilist, collect_attrs=collect_attrs,
                            inst_cat=inst_cat, known_cat=known_cat,
                            patterns=fmri_strings, raise_unmatched=True,
                            ranked=ranked, return_fmris=True,
                            return_metadata=True, variants=True):
                                release = build_release = branch = \
                                    packaging_date = None

                                pub, name, version = pfmri.tuple()
                                alt_pub = None
                                if pkg_pub_map:
                                        alt_pub = \
                                            pkg_pub_map[pub][name][str(version)]

                                if PackageInfo.IDENTITY in info_needed:
                                        release = version.release
                                        build_release = version.build_release
                                        branch = version.branch
                                        packaging_date = \
                                            version.get_timestamp().strftime(
                                            "%c")
                                else:
                                        pub = name = version = None

                                links = hardlinks = files = dirs = \
                                    csize = size = licenses = cat_info = \
                                    description = None

                                if PackageInfo.CATEGORIES in info_needed:
                                        cat_info = [
                                            PackageCategory(scheme, cat)
                                            for scheme, cat in cats
                                        ]

                                ret_cat_data = cat_opts & info_needed
                                dependencies = None
                                unsupported = False
                                if ret_cat_data:
                                        try:
                                                ignored, description, ignored, \
                                                    dependencies = \
                                                    _get_pkg_cat_data(pkg_cat,
                                                        ret_cat_data,
                                                        excludes=excludes,
                                                        pfmri=pfmri)
                                        except apx.InvalidPackageErrors:
                                                # If the information can't be
                                                # retrieved because the manifest
                                                # can't be parsed, mark it and
                                                # continue.
                                                unsupported = True

                                if dependencies is None:
                                        dependencies = misc.EmptyI

                                mfst = None
                                if not unsupported and \
                                    (frozenset([PackageInfo.SIZE,
                                    PackageInfo.LICENSES]) | act_opts) & \
                                    info_needed:
                                        try:
                                                mfst = self._img.get_manifest(
                                                    pfmri, alt_pub=alt_pub)
                                        except apx.InvalidPackageErrors:
                                                # If the information can't be
                                                # retrieved because the manifest
                                                # can't be parsed, mark it and
                                                # continue.
                                                unsupported = True

                                if mfst is not None:
                                        if PackageInfo.LICENSES in info_needed:
                                                licenses = self.__licenses(pfmri,
                                                    mfst, alt_pub=alt_pub)

                                        if PackageInfo.SIZE in info_needed:
                                                size, csize = mfst.get_size(
                                                    excludes=excludes)

                                        if act_opts & info_needed:
                                                if PackageInfo.LINKS in info_needed:
                                                        links = list(
                                                            mfst.gen_key_attribute_value_by_type(
                                                            "link", excludes))
                                                if PackageInfo.HARDLINKS in info_needed:
                                                        hardlinks = list(
                                                            mfst.gen_key_attribute_value_by_type(
                                                            "hardlink", excludes))
                                                if PackageInfo.FILES in info_needed:
                                                        files = list(
                                                            mfst.gen_key_attribute_value_by_type(
                                                            "file", excludes))
                                                if PackageInfo.DIRS in info_needed:
                                                        dirs = list(
                                                            mfst.gen_key_attribute_value_by_type(
                                                            "dir", excludes))
                                elif PackageInfo.SIZE in info_needed:
                                        size = csize = 0

                                # Trim response set.
                                last_install = None
                                last_update = None
                                if PackageInfo.STATE in info_needed:
                                        if unsupported is True and \
                                            PackageInfo.UNSUPPORTED not in states:
                                                # Mark package as
                                                # unsupported so that
                                                # caller can decide
                                                # what to do.
                                                states = set(states)
                                                states.add(
                                                    PackageInfo.UNSUPPORTED)

                                        if "last-update" in mdata:
                                                last_update = catalog.basic_ts_to_datetime(
                                                    mdata["last-update"]).strftime("%c")
                                        if "last-install" in mdata:
                                                last_install = catalog.basic_ts_to_datetime(
                                                    mdata["last-install"]).strftime("%c")
                                else:
                                        states = misc.EmptyI

                                if PackageInfo.CATEGORIES not in info_needed:
                                        cats = None
                                if PackageInfo.SUMMARY in info_needed:
                                        if summary is None:
                                                summary = ""
                                else:
                                        summary = None

                                pis.append(PackageInfo(pkg_stem=name,
                                    summary=summary, category_info_list=cat_info,
                                    states=states, publisher=pub, version=release,
                                    build_release=build_release, branch=branch,
                                    packaging_date=packaging_date, size=size,
                                    csize=csize, pfmri=pfmri, licenses=licenses,
                                    links=links, hardlinks=hardlinks, files=files,
                                    dirs=dirs, dependencies=dependencies,
                                    description=description, attrs=attrs,
                                    last_update=last_update,
                                    last_install=last_install))
                except apx.InventoryException as e:
                        if e.illegal:
                                self.log_operation_end(
                                    result=RESULT_FAILED_BAD_REQUEST)
                        rval[self.INFO_MISSING] = e.notfound
                        rval[self.INFO_ILLEGALS] = e.illegal
                else:
                        if pis:
                                self.log_operation_end()
                        else:
                                self.log_operation_end(
                                    result=RESULT_NOTHING_TO_DO)
                return rval

        def can_be_canceled(self):
                """Returns true if the API is in a cancelable state."""
                return self.__can_be_canceled

        def _disable_cancel(self):
                """Sets_can_be_canceled to False in a way that prevents missed
                wakeups.  This may raise CanceledException, if a
                cancellation is pending."""

                self.__cancel_lock.acquire()
                if self.__canceling:
                        self.__cancel_lock.release()
                        self._img.transport.reset()
                        raise apx.CanceledException()
                else:
                        self.__set_can_be_canceled(False)
                self.__cancel_lock.release()

        def _enable_cancel(self):
                """Sets can_be_canceled to True while grabbing the cancel
                locks.  The caller must still hold the activity lock while
                calling this function."""

                self.__cancel_lock.acquire()
                self.__set_can_be_canceled(True)
                self.__cancel_lock.release()

        def __set_can_be_canceled(self, status):
                """Private method. Handles the details of changing the
                cancelable state."""
                assert self.__cancel_lock._is_owned()

                # If caller requests a change to current state there is
                # nothing to do.
                if self.__can_be_canceled == status:
                        return

                if status:
                        # Callers must hold activity lock for operations
                        # that they will make cancelable.
                        assert self._activity_lock._is_owned()
                        # In any situation where the caller holds the activity
                        # lock and wants to set cancelable to true, a cancel
                        # should not already be in progress.  This is because
                        # it should not be possible to invoke cancel until
                        # this routine has finished.  Assert that we're not
                        # canceling.
                        assert not self.__canceling

                self.__can_be_canceled = status
                if self.__cancel_state_callable:
                        self.__cancel_state_callable(self.__can_be_canceled)

        def reset(self):
                """Resets the API back the initial state. Note:
                this does not necessarily return the disk to its initial state
                since the indexes or download cache may have been changed by
                the prepare method."""
                self._acquire_activity_lock()
                self.__reset_unlock()
                self._activity_lock.release()

        def __reset_unlock(self):
                """Private method. Provides a way to reset without taking the
                activity lock. Should only be called by a thread which already
                holds the activity lock."""

                assert self._activity_lock._is_owned()

                # This needs to be done first so that find_root can use it.
                self.__progresstracker.reset()

                # Ensure alternate sources are always cleared in an
                # exception scenario.
                self.__set_img_alt_sources(None)
                self.__alt_sources = {}

                self._img.cleanup_downloads()
                # Cache transport statistics about problematic repo sources
                repo_status = self._img.transport.repo_status
                self._img.transport.shutdown()

                # Recreate the image object using the path the api
                # object was created with instead of the current path.
                self._img = image.Image(self._img_path,
                    progtrack=self.__progresstracker,
                    user_provided_dir=True,
                    cmdpath=self.cmdpath)
                self._img.blocking_locks = self.__blocking_locks

                self._img.transport.repo_status = repo_status

                lin = None
                if self._img.linked.ischild():
                        lin = self._img.linked.child_name
                self.__progresstracker.set_linked_name(lin)

                self.__plan_desc = None
                self.__planned_children = False
                self.__plan_type = None
                self.__prepared = False
                self.__executed = False
                self.__be_name = None

                self._cancel_cleanup_exception()

        def __check_cancel(self):
                """Private method. Provides a callback method for internal
                code to use to determine whether the current action has been
                canceled."""
                return self.__canceling

        def _cancel_cleanup_exception(self):
                """A private method that is called from exception handlers.
                This is not needed if the method calls reset unlock,
                which will call this method too.  This catches the case
                where a caller might have called cancel and gone to sleep,
                but the requested operation failed with an exception before
                it could raise a CanceledException."""

                self.__cancel_lock.acquire()
                self.__set_can_be_canceled(False)
                self.__canceling = False
                # Wake up any threads that are waiting on this aborted
                # operation.
                self.__cancel_cv.notify_all()
                self.__cancel_lock.release()

        def _cancel_done(self):
                """A private method that wakes any threads that have been
                sleeping, waiting for a cancellation to finish."""

                self.__cancel_lock.acquire()
                if self.__canceling:
                        self.__canceling = False
                        self.__cancel_cv.notify_all()
                self.__cancel_lock.release()

        def cancel(self):
                """Used for asynchronous cancelation. It returns the API
                to the state it was in prior to the current method being
                invoked.  Canceling during a plan phase returns the API to
                its initial state. Canceling during prepare puts the API
                into the state it was in just after planning had completed.
                Plan execution cannot be canceled. A call to this method blocks
                until the cancellation has happened. Note: this does not
                necessarily return the disk to its initial state since the
                indexes or download cache may have been changed by the
                prepare method."""

                self.__cancel_lock.acquire()

                if not self.__can_be_canceled:
                        self.__cancel_lock.release()
                        return False

                self.__set_can_be_canceled(False)
                self.__canceling = True
                # Wait until the cancelled operation wakes us up.
                self.__cancel_cv.wait()
                self.__cancel_lock.release()
                return True

        def clear_history(self):
                """Discard history information about in-progress operations."""
                self._img.history.clear()

        def __set_history_PlanCreationException(self, e):
                if e.unmatched_fmris or e.multiple_matches or \
                    e.missing_matches or e.illegal:
                        self.log_operation_end(error=e,
                            result=RESULT_FAILED_BAD_REQUEST)
                else:
                        self.log_operation_end(error=e)

        @_LockedGenerator()
        def local_search(self, query_lst):
                """local_search takes a list of Query objects and performs
                each query against the installed packages of the image."""

                l = query_p.QueryLexer()
                l.build()
                qp = query_p.QueryParser(l)
                ssu = None
                for i, q in enumerate(query_lst):
                        try:
                                query = qp.parse(q.text)
                                query_rr = qp.parse(q.text)
                                if query_rr.remove_root(self._img.root):
                                        query.add_or(query_rr)
                                if q.return_type == \
                                    query_p.Query.RETURN_PACKAGES:
                                        query.propagate_pkg_return()
                        except query_p.BooleanQueryException as e:
                                raise apx.BooleanQueryException(e)
                        except query_p.ParseError as e:
                                raise apx.ParseError(e)
                        self._img.update_index_dir()
                        assert self._img.index_dir
                        try:
                                query.set_info(num_to_return=q.num_to_return,
                                    start_point=q.start_point,
                                    index_dir=self._img.index_dir,
                                    get_manifest_path=\
                                        self._img.get_manifest_path,
                                    gen_installed_pkg_names=\
                                        self._img.gen_installed_pkg_names,
                                    case_sensitive=q.case_sensitive)
                                res = query.search(
                                    self._img.gen_installed_pkgs,
                                    self._img.get_manifest_path,
                                    self._img.list_excludes())
                        except search_errors.InconsistentIndexException as e:
                                raise apx.InconsistentIndexException(e)
                        # i is being inserted to track which query the results
                        # are for.  None is being inserted since there is no
                        # publisher being searched against.
                        try:
                                for r in res:
                                        yield i, None, r
                        except apx.SlowSearchUsed as e:
                                ssu = e
                if ssu:
                        raise ssu

        @staticmethod
        def __parse_v_0(line, pub, v):
                """This function parses the string returned by a version 0
                search server and puts it into the expected format of
                (query_number, publisher, (version, return_type, (results))).

                "query_number" in the return value is fixed at 0 since search
                v0 servers cannot accept multiple queries in a single HTTP
                request."""

                line = line.strip()
                fields = line.split(None, 3)
                return (0, pub, (v, Query.RETURN_ACTIONS, (fields[:4])))

        @staticmethod
        def __parse_v_1(line, pub, v):
                """This function parses the string returned by a version 1
                search server and puts it into the expected format of
                (query_number, publisher, (version, return_type, (results)))
                If it receives a line it can't parse, it raises a
                ServerReturnError."""

                fields = line.split(None, 2)
                if len(fields) != 3:
                        raise apx.ServerReturnError(line)
                try:
                        return_type = int(fields[1])
                        query_num = int(fields[0])
                except ValueError:
                        raise apx.ServerReturnError(line)
                if return_type == Query.RETURN_ACTIONS:
                        subfields = fields[2].split(None, 2)
                        pfmri = fmri.PkgFmri(subfields[0])
                        return pfmri, (query_num, pub, (v, return_type,
                            (pfmri, unquote(subfields[1]),
                            subfields[2])))
                elif return_type == Query.RETURN_PACKAGES:
                        pfmri = fmri.PkgFmri(fields[2])
                        return pfmri, (query_num, pub, (v, return_type, pfmri))
                else:
                        raise apx.ServerReturnError(line)

        @_LockedGenerator()
        def remote_search(self, query_str_and_args_lst, servers=None,
            prune_versions=True):
                """This function takes a list of Query objects, and optionally
                a list of servers to search against.  It performs each query
                against each server and yields the results in turn.  If no
                servers are provided, the search is conducted against all
                active servers known by the image.

                The servers argument is a list of servers in two possible
                forms: the old deprecated form of a publisher, in a
                dictionary, or a Publisher object.

                A call to this function returns a generator that holds
                API locks.  Callers must either iterate through all of the
                results, or call close() on the resulting object.  Otherwise
                it is possible to get deadlocks or NRLock reentrance
                exceptions."""

                failed = []
                invalid = []
                unsupported = []

                if not servers:
                        servers = self._img.gen_publishers()

                new_qs = []
                l = query_p.QueryLexer()
                l.build()
                qp = query_p.QueryParser(l)
                for q in query_str_and_args_lst:
                        try:
                                query = qp.parse(q.text)
                                query_rr = qp.parse(q.text)
                                if query_rr.remove_root(self._img.root):
                                        query.add_or(query_rr)
                                if q.return_type == \
                                    query_p.Query.RETURN_PACKAGES:
                                        query.propagate_pkg_return()
                                new_qs.append(query_p.Query(str(query),
                                    q.case_sensitive, q.return_type,
                                    q.num_to_return, q.start_point))
                        except query_p.BooleanQueryException as e:
                                raise apx.BooleanQueryException(e)
                        except query_p.ParseError as e:
                                raise apx.ParseError(e)

                query_str_and_args_lst = new_qs

                incorp_info, inst_stems = self.get_incorp_info()

                slist = []
                for entry in servers:
                        if isinstance(entry, dict):
                                origin = entry["origin"]
                                try:
                                        pub = self._img.get_publisher(
                                            origin=origin)
                                        pub_uri = publisher.RepositoryURI(
                                            origin)
                                        repo = publisher.Repository(
                                            origins=[pub_uri])
                                except apx.UnknownPublisher:
                                        pub = publisher.RepositoryURI(origin)
                                        repo = publisher.Repository(
                                            origins=[pub])
                                slist.append((pub, repo, origin))
                                continue

                        # Must be a publisher object.
                        osets = entry.get_origin_sets()
                        if not osets:
                                continue
                        for repo in osets:
                                slist.append((entry, repo, entry.prefix))

                for pub, alt_repo, descriptive_name in slist:
                        if self.__canceling:
                                raise apx.CanceledException()

                        try:
                                res = self._img.transport.do_search(pub,
                                    query_str_and_args_lst,
                                    ccancel=self.__check_cancel,
                                    alt_repo=alt_repo)
                        except apx.CanceledException:
                                raise
                        except apx.NegativeSearchResult:
                                continue
                        except (apx.InvalidDepotResponseException,
                            apx.TransportError) as e:
                                # Alternate source failed portal test or can't
                                # be contacted at all.
                                failed.append((descriptive_name, e))
                                continue
                        except apx.UnsupportedSearchError as e:
                                unsupported.append((descriptive_name, e))
                                continue
                        except apx.MalformedSearchRequest as e:
                                ex = self._validate_search(
                                    query_str_and_args_lst)
                                if ex:
                                        raise ex
                                failed.append((descriptive_name, e))
                                continue

                        try:
                                if not self.validate_response(res, 1):
                                        invalid.append(descriptive_name)
                                        continue
                                for line in res:
                                        pfmri, ret = self.__parse_v_1(line, pub,
                                            1)
                                        pstem = pfmri.pkg_name
                                        pver = pfmri.version
                                        # Skip this package if a newer version
                                        # is already installed and version
                                        # pruning is enabled.
                                        if prune_versions and \
                                            pstem in inst_stems and \
                                            pver < inst_stems[pstem]:
                                                continue
                                        # Return this result if version pruning
                                        # is disabled, the package is not
                                        # incorporated, or the version of the
                                        # package matches the incorporation.
                                        if not prune_versions or \
                                            pstem not in incorp_info or \
                                            pfmri.version.is_successor(
                                                incorp_info[pstem],
                                                pkg.version.CONSTRAINT_AUTO):
                                                yield ret

                        except apx.CanceledException:
                                raise
                        except apx.TransportError as e:
                                failed.append((descriptive_name, e))
                                continue

                if failed or invalid or unsupported:
                        raise apx.ProblematicSearchServers(failed,
                            invalid, unsupported)

        def get_incorp_info(self):
                """This function returns a mapping of package stems to the
                version at which they are incorporated, if they are
                incorporated, and the version at which they are installed, if
                they are installed."""

                # This maps fmris to the version at which they're incorporated.
                inc_vers = {}
                inst_stems = {}

                img_cat = self._img.get_catalog(
                    self._img.IMG_CATALOG_INSTALLED)
                cat_info = frozenset([img_cat.DEPENDENCY])

                # The incorporation list should include all installed,
                # incorporated packages from all publishers.
                for pfmri, actions in img_cat.actions(cat_info):
                        inst_stems[pfmri.pkg_name] = pfmri.version
                        for a in actions:
                                if a.name != "depend" or \
                                    a.attrs["type"] != "incorporate":
                                        continue
                                # Record incorporated packages.
                                tgt = fmri.PkgFmri(a.attrs["fmri"])
                                tver = tgt.version
                                # incorporates without a version should be
                                # ignored.
                                if not tver:
                                        continue
                                over = inc_vers.get(
                                    tgt.pkg_name, None)

                                # In case this package has been
                                # incorporated more than once,
                                # use the newest version.
                                if over > tver:
                                        continue
                                inc_vers[tgt.pkg_name] = tver
                return inc_vers, inst_stems

        @staticmethod
        def __unconvert_return_type(v):
                return v == query_p.Query.RETURN_ACTIONS

        def _validate_search(self, query_str_lst):
                """Called by remote search if server responds that the
                request was invalid.  In this case, parse the query on
                the client-side and determine what went wrong."""

                for q in query_str_lst:
                        l = query_p.QueryLexer()
                        l.build()
                        qp = query_p.QueryParser(l)
                        try:
                                query = qp.parse(q.text)
                        except query_p.BooleanQueryException as e:
                                return apx.BooleanQueryException(e)
                        except query_p.ParseError as e:
                                return apx.ParseError(e)

                return None

        def rebuild_search_index(self):
                """Rebuilds the search indexes.  Removes all
                existing indexes and replaces them from scratch rather than
                performing the incremental update which is usually used.
                This is useful for times when the index for the client has
                been corrupted."""
                self._img.update_index_dir()
                self.log_operation_start("rebuild-index")
                if not os.path.isdir(self._img.index_dir):
                        self._img.mkdirs()
                try:
                        ind = indexer.Indexer(self._img, self._img.get_manifest,
                            self._img.get_manifest_path,
                            self.__progresstracker, self._img.list_excludes())
                        ind.rebuild_index_from_scratch(
                            self._img.gen_installed_pkgs())
                except search_errors.ProblematicPermissionsIndexException as e:
                        error = apx.ProblematicPermissionsIndexException(e)
                        self.log_operation_end(error=error)
                        raise error
                else:
                        self.log_operation_end()

        def get_manifest(self, pfmri, all_variants=True, repos=None):
                """Returns the Manifest object for the given package FMRI.

                'all_variants' is an optional boolean value indicating whther
                the manifest should include metadata for all variants and
                facets.

                'repos' is a list of URI strings or RepositoryURI objects that
                represent the locations of additional sources of package data to
                use during the planned operation.
                """

                alt_pub = None
                if repos:
                        pkg_pub_map, ignored, known_cat, inst_cat = \
                            self.__get_alt_pkg_data(repos)
                        alt_pub = pkg_pub_map.get(pfmri.publisher, {}).get(
                            pfmri.pkg_name, {}).get(str(pfmri.version), None)
                return self._img.get_manifest(pfmri,
                    ignore_excludes=all_variants, alt_pub=alt_pub)

        @staticmethod
        def validate_response(res, v):
                """This function is used to determine whether the first
                line returned from a server is expected.  This ensures that
                search is really communicating with a search-enabled server."""

                try:
                        s = next(res)
                        return s == Query.VALIDATION_STRING[v]
                except StopIteration:
                        return False

        def add_publisher(self, pub, refresh_allowed=True,
            approved_cas=misc.EmptyI, revoked_cas=misc.EmptyI,
            search_after=None, search_before=None, search_first=None,
            unset_cas=misc.EmptyI):
                """Add the provided publisher object to the image
                configuration."""
                try:
                        self._img.add_publisher(pub,
                            refresh_allowed=refresh_allowed,
                            progtrack=self.__progresstracker,
                            approved_cas=approved_cas, revoked_cas=revoked_cas,
                            search_after=search_after,
                            search_before=search_before,
                            search_first=search_first,
                            unset_cas=unset_cas)
                finally:
                        self._img.cleanup_downloads()

        def get_highest_ranked_publisher(self):
                """Returns the highest ranked publisher object for the image."""
                return self._img.get_highest_ranked_publisher()

        def get_publisher(self, prefix=None, alias=None, duplicate=False):
                """Retrieves a publisher object matching the provided prefix
                (name) or alias.

                'duplicate' is an optional boolean value indicating whether
                a copy of the publisher object should be returned instead
                of the original.
                """
                pub = self._img.get_publisher(prefix=prefix, alias=alias)
                if duplicate:
                        # Never return the original so that changes to the
                        # retrieved object are not reflected until
                        # update_publisher is called.
                        return copy.copy(pub)
                return pub

        @_LockedCancelable()
        def get_publisherdata(self, pub=None, repo=None):
                """Attempts to retrieve publisher configuration information from
                the specified publisher's repository or the provided repository.
                If successful, it will either return an empty list (in the case
                that the repository supports the operation, but doesn't offer
                configuration information) or a list of Publisher objects.
                If this operation is not supported by the publisher or the
                specified repository, an UnsupportedRepositoryOperation
                exception will be raised.

                'pub' is an optional Publisher object.

                'repo' is an optional RepositoryURI object.

                Either 'pub' or 'repo' must be provided."""

                assert (pub or repo) and not (pub and repo)

                # Transport accepts either type of object, but a distinction is
                # made in the client API for clarity.
                pub = max(pub, repo)

                return self._img.transport.get_publisherdata(pub,
                    ccancel=self.__check_cancel)

        def get_publishers(self, duplicate=False):
                """Returns a list of the publisher objects for the current
                image.

                'duplicate' is an optional boolean value indicating whether
                copies of the publisher objects should be returned instead
                of the originals.
                """

                res = self._img.get_sorted_publishers()
                if duplicate:
                        return [copy.copy(p) for p in res]
                return res

        def get_publisher_last_update_time(self, prefix=None, alias=None):
                """Returns a datetime object representing the last time the
                catalog for a publisher was modified or None."""

                if alias:
                        pub = self.get_publisher(alias=alias)
                else:
                        pub = self.get_publisher(prefix=prefix)

                if pub.disabled:
                        return None

                dt = None
                self._acquire_activity_lock()
                try:
                        self._enable_cancel()
                        try:
                                dt = pub.catalog.last_modified
                        except:
                                self.__reset_unlock()
                                raise
                        try:
                                self._disable_cancel()
                        except apx.CanceledException:
                                self._cancel_done()
                                raise
                finally:
                        self._activity_lock.release()
                return dt

        def has_publisher(self, prefix=None, alias=None):
                """Returns a boolean value indicating whether a publisher using
                the given prefix or alias exists."""
                return self._img.has_publisher(prefix=prefix, alias=alias)

        def remove_publisher(self, prefix=None, alias=None):
                """Removes a publisher object matching the provided prefix
                (name) or alias."""
                self._img.remove_publisher(prefix=prefix, alias=alias,
                    progtrack=self.__progresstracker)

                self.__remove_unused_client_certificates()

        def update_publisher(self, pub, refresh_allowed=True, search_after=None,
            search_before=None, search_first=None):
                """Replaces an existing publisher object with the provided one
                using the _source_object_id identifier set during copy.

                'refresh_allowed' is an optional boolean value indicating
                whether a refresh of publisher metadata (such as its catalog)
                should be performed if transport information is changed for a
                repository, mirror, or origin.  If False, no attempt will be
                made to retrieve publisher metadata."""

                self._acquire_activity_lock()
                try:
                        self._disable_cancel()
                        with self._img.locked_op("update-publisher"):
                                return self.__update_publisher(pub,
                                    refresh_allowed=refresh_allowed,
                                    search_after=search_after,
                                    search_before=search_before,
                                    search_first=search_first)
                except apx.CanceledException as e:
                        self._cancel_done()
                        raise
                finally:
                        self._img.cleanup_downloads()
                        self._activity_lock.release()

        def __update_publisher(self, pub, refresh_allowed=True,
            search_after=None, search_before=None, search_first=None):
                """Private publisher update method; caller responsible for
                locking."""

                assert (not search_after and not search_before) or \
                    (not search_after and not search_first) or \
                    (not search_before and not search_first)

                # Before continuing, validate SSL information.
                try:
                        self._img.check_cert_validity(pubs=[pub])
                except apx.ExpiringCertificate as e:
                        logger.warning(str(e))

                def origins_changed(oldr, newr):
                        old_origins = set([
                            (o.uri, o.ssl_cert,
                                o.ssl_key, tuple(sorted(o.proxies)), o.disabled)
                            for o in oldr.origins
                        ])
                        new_origins = set([
                            (o.uri, o.ssl_cert,
                                o.ssl_key, tuple(sorted(o.proxies)), o.disabled)
                            for o in newr.origins
                        ])
                        return (new_origins - old_origins), \
                            new_origins.symmetric_difference(old_origins)

                def need_refresh(oldo, newo):
                        if newo.disabled:
                                # The publisher is disabled, so no refresh
                                # should be performed.
                                return False

                        if oldo.disabled and not newo.disabled:
                                # The publisher has been re-enabled, so
                                # retrieve the catalog.
                                return True

                        oldr = oldo.repository
                        newr = newo.repository
                        if newr._source_object_id != id(oldr):
                                # Selected repository has changed.
                                return True
                        # If any were added or removed, refresh.
                        return len(origins_changed(oldr, newr)[1]) != 0

                refresh_catalog = False
                disable = False
                orig_pub = None

                # Configuration must be manipulated directly.
                publishers = self._img.cfg.publishers

                # First, attempt to match the updated publisher object to an
                # existing one using the object id that was stored during
                # copy().
                for key, old in six.iteritems(publishers):
                        if pub._source_object_id == id(old):
                                # Store the new publisher's id and the old
                                # publisher object so it can be restored if the
                                # update operation fails.
                                orig_pub = (id(pub), old)
                                break

                if not orig_pub:
                        # If a matching publisher couldn't be found and
                        # replaced, something is wrong (client api usage
                        # error).
                        raise apx.UnknownPublisher(pub)

                # Next, be certain that the publisher's prefix and alias
                # are not already in use by another publisher.
                for key, old in six.iteritems(publishers):
                        if pub._source_object_id == id(old):
                                # Don't check the object we're replacing.
                                continue

                        if pub.prefix == old.prefix or \
                            pub.prefix == old.alias or \
                            pub.alias and (pub.alias == old.alias or
                            pub.alias == old.prefix):
                                raise apx.DuplicatePublisher(pub)

                # Next, determine what needs updating and add the updated
                # publisher.
                for key, old in six.iteritems(publishers):
                        if pub._source_object_id == id(old):
                                old = orig_pub[-1]
                                if need_refresh(old, pub):
                                        refresh_catalog = True
                                if not old.disabled and pub.disabled:
                                        disable = True

                                # Now remove the old publisher object using the
                                # iterator key if the prefix has changed.
                                if key != pub.prefix:
                                        del publishers[key]

                                # Prepare the new publisher object.
                                pub.meta_root = \
                                    self._img._get_publisher_meta_root(
                                    pub.prefix)
                                pub.transport = self._img.transport

                                # Finally, add the new publisher object.
                                publishers[pub.prefix] = pub
                                break

                def cleanup():
                        # Attempting to unpack a non-sequence%s;
                        # pylint: disable=W0633
                        new_id, old_pub = orig_pub
                        for new_pfx, new_pub in six.iteritems(publishers):
                                if id(new_pub) == new_id:
                                        publishers[old_pub.prefix] = old_pub
                                        break

                repo = pub.repository

                validate = origins_changed(orig_pub[-1].repository,
                    pub.repository)[0]

                try:
                        if disable or (not repo.origins and
                            orig_pub[-1].repository.origins):
                                # Remove the publisher's metadata (such as
                                # catalogs, etc.).  This only needs to be done
                                # in the event that a publisher is disabled or
                                # has transitioned from having origins to not
                                # having any at all; in any other case (the
                                # origins changing, etc.), refresh() will do the
                                # right thing.
                                self._img.remove_publisher_metadata(pub)
                        elif not pub.disabled and not refresh_catalog:
                                refresh_catalog = pub.needs_refresh

                        if refresh_catalog and refresh_allowed:
                                # One of the publisher's repository origins may
                                # have changed, so the publisher needs to be
                                # revalidated.

                                if validate:
                                        self._img.transport.valid_publisher_test(
                                            pub)

                                # Validate all new origins against publisher
                                # configuration.
                                for uri, ssl_cert, ssl_key, proxies, disabled \
                                    in validate:
                                        repo = publisher.RepositoryURI(uri,
                                            ssl_cert=ssl_cert, ssl_key=ssl_key,
                                            proxies=proxies, disabled=disabled)
                                        pub.validate_config(repo)

                                self.__refresh(pubs=[pub], immediate=True,
                                    ignore_unreachable=False)
                        elif refresh_catalog:
                                # Something has changed (such as a repository
                                # origin) for the publisher, so a refresh should
                                # occur, but isn't currently allowed.  As such,
                                # clear the last_refreshed time so that the next
                                # time the client checks to see if a refresh is
                                # needed and is allowed, one will be performed.
                                pub.last_refreshed = None
                except Exception as e:
                        # If any of the above fails, the original publisher
                        # information needs to be restored so that state is
                        # consistent.
                        cleanup()
                        raise
                except:
                        # If any of the above fails, the original publisher
                        # information needs to be restored so that state is
                        # consistent.
                        cleanup()
                        raise

                if search_first:
                        self._img.set_highest_ranked_publisher(
                            prefix=pub.prefix)
                elif search_before:
                        self._img.pub_search_before(pub.prefix, search_before)
                elif search_after:
                        self._img.pub_search_after(pub.prefix, search_after)

                # Successful; so save configuration.
                self._img.save_config()

                self.__remove_unused_client_certificates()

        def __remove_unused_client_certificates(self):
                """Remove unused client certificate files"""

                # Get certificate files currently in use.
                ssl_path = os.path.join(self._img.imgdir, "ssl")
                current_file_list = set()
                pubs = self._img.get_publishers()
                for p in pubs:
                        pub = pubs[p]
                        for origin in pub.repository.origins:
                                current_file_list.add(origin.ssl_key)
                                current_file_list.add(origin.ssl_cert)

                # Remove files found in ssl directory that
                # are not in use by publishers.
                for f in os.listdir(ssl_path):
                        path = os.path.join(ssl_path, f)
                        if path not in current_file_list:
                                try:
                                        portable.remove(path)
                                except:
                                        continue

        def log_operation_end(self, error=None, result=None,
            release_notes=None):
                """Marks the end of an operation to be recorded in image
                history.

                'result' should be a pkg.client.history constant value
                representing the outcome of an operation.  If not provided,
                and 'error' is provided, the final result of the operation will
                be based on the class of 'error' and 'error' will be recorded
                for the current operation.  If 'result' and 'error' is not
                provided, success is assumed."""
                self._img.history.log_operation_end(error=error, result=result,
                release_notes=release_notes)

        def log_operation_error(self, error):
                """Adds an error to the list of errors to be recorded in image
                history for the current opreation."""
                self._img.history.log_operation_error(error)

        def log_operation_start(self, name):
                """Marks the start of an operation to be recorded in image
                history."""
                # If an operation is going to be discarded, then don't take the
                # performance hit of actually getting the BE info.
                if name in history.DISCARDED_OPERATIONS:
                        be_name, be_uuid = None, None
                else:
                        be_name, be_uuid = bootenv.BootEnv.get_be_name(
                            self._img.root)
                self._img.history.log_operation_start(name,
                    be_name=be_name, be_uuid=be_uuid)

        def parse_liname(self, name, unknown_ok=False):
                """Parse a linked image name string and return a
                LinkedImageName object.  If "unknown_ok" is true then
                liname must correspond to an existing linked image.  If
                "unknown_ok" is false and liname doesn't correspond to
                an existing linked image then liname must be a
                syntactically valid and fully qualified linked image
                name."""

                return self._img.linked.parse_name(name, unknown_ok=unknown_ok)

        def parse_p5i(self, data=None, fileobj=None, location=None):
                """Reads the pkg(5) publisher JSON formatted data at 'location'
                or from the provided file-like object 'fileobj' and returns a
                list of tuples of the format (publisher object, pkg_names).
                pkg_names is a list of strings representing package names or
                FMRIs.  If any pkg_names not specific to a publisher were
                provided, the last tuple returned will be of the format (None,
                pkg_names).

                'data' is an optional string containing the p5i data.

                'fileobj' is an optional file-like object that must support a
                'read' method for retrieving data.

                'location' is an optional string value that should either start
                with a leading slash and be pathname of a file or a URI string.
                If it is a URI string, supported protocol schemes are 'file',
                'ftp', 'http', and 'https'.

                'data' or 'fileobj' or 'location' must be provided."""

                return p5i.parse(data=data, fileobj=fileobj, location=location)

        def parse_fmri_patterns(self, patterns):
                """A generator function that yields a list of tuples of the form
                (pattern, error, fmri, matcher) based on the provided patterns,
                where 'error' is any exception encountered while parsing the
                pattern, 'fmri' is the resulting FMRI object, and 'matcher' is
                one of the following constant values:

                        MATCH_EXACT
                                Indicates that the name portion of the pattern
                                must match exactly and the version (if provided)
                                must be considered a successor or equal to the
                                target FMRI.

                        MATCH_FMRI
                                Indicates that the name portion of the pattern
                                must be a proper subset and the version (if
                                provided) must be considered a successor or
                                equal to the target FMRI.

                        MATCH_GLOB
                                Indicates that the name portion of the pattern
                                uses fnmatch rules for pattern matching (shell-
                                style wildcards) and that the version can either
                                match exactly, match partially, or contain
                                wildcards.
                """

                for pat in patterns:
                        error = None
                        matcher = None
                        npat = None
                        try:
                                parts = pat.split("@", 1)
                                pat_stem = parts[0]
                                pat_ver = None
                                if len(parts) > 1:
                                        pat_ver = parts[1]

                                if "*" in pat_stem or "?" in pat_stem:
                                        matcher = self.MATCH_GLOB
                                elif pat_stem.startswith("pkg:/") or \
                                    pat_stem.startswith("/"):
                                        matcher = self.MATCH_EXACT
                                else:
                                        matcher = self.MATCH_FMRI

                                if matcher == self.MATCH_GLOB:
                                        npat = fmri.MatchingPkgFmri(pat_stem)
                                else:
                                        npat = fmri.PkgFmri(pat_stem)

                                if not pat_ver:
                                        # Do nothing.
                                        pass
                                elif "*" in pat_ver or "?" in pat_ver or \
                                    pat_ver == "latest":
                                        npat.version = \
                                            pkg.version.MatchingVersion(pat_ver)
                                else:
                                        npat.version = \
                                            pkg.version.Version(pat_ver)

                        except (fmri.FmriError, pkg.version.VersionError) as e:
                                # Whatever the error was, return it.
                                error = e
                        yield (pat, error, npat, matcher)

        def purge_history(self):
                """Deletes all client history."""
                be_name, be_uuid = bootenv.BootEnv.get_be_name(self._img.root)
                self._img.history.purge(be_name=be_name, be_uuid=be_uuid)

        def update_format(self):
                """Attempt to update the on-disk format of the image to the
                newest version.  Returns a boolean indicating whether any action
                was taken."""

                self._acquire_activity_lock()
                try:
                        self._disable_cancel()
                        self._img.allow_ondisk_upgrade = True
                        return self._img.update_format(
                            progtrack=self.__progresstracker)
                except apx.CanceledException as e:
                        self._cancel_done()
                        raise
                finally:
                        self._activity_lock.release()

        def write_p5i(self, fileobj, pkg_names=None, pubs=None):
                """Writes the publisher, repository, and provided package names
                to the provided file-like object 'fileobj' in JSON p5i format.

                'fileobj' is only required to have a 'write' method that accepts
                data to be written as a parameter.

                'pkg_names' is a dict of lists, tuples, or sets indexed by
                publisher prefix that contain package names, FMRI strings, or
                package info objects.  A prefix of "" can be used for packages
                that are not specific to a publisher.

                'pubs' is an optional list of publisher prefixes or Publisher
                objects.  If not provided, the information for all publishers
                (excluding those disabled) will be output."""

                if not pubs:
                        plist = [
                            p for p in self.get_publishers()
                            if not p.disabled
                        ]
                else:
                        plist = []
                        for p in pubs:
                                if not isinstance(p, publisher.Publisher):
                                        plist.append(self._img.get_publisher(
                                            prefix=p, alias=p))
                                else:
                                        plist.append(p)

                # Transform PackageInfo object entries into PkgFmri entries
                # before passing them to the p5i module.
                new_pkg_names = {}
                for pub in pkg_names:
                        pkglist = []
                        for p in pkg_names[pub]:
                                if isinstance(p, PackageInfo):
                                        pkglist.append(p.fmri)
                                else:
                                        pkglist.append(p)
                        new_pkg_names[pub] = pkglist
                p5i.write(fileobj, plist, pkg_names=new_pkg_names)

        def write_syspub(self, path, prefixes, version):
                """Write the syspub/version response to the provided path."""
                if version != 0:
                        raise apx.UnsupportedP5SVersion(version)

                pubs = [
                    p for p in self.get_publishers()
                    if p.prefix in prefixes
                ]
                fd, fp = tempfile.mkstemp()
                try:
                        fh = os.fdopen(fd, "w")
                        p5s.write(fh, pubs, self._img.cfg)
                        fh.close()
                        portable.rename(fp, path)
                except:
                        if os.path.exists(fp):
                                portable.remove(fp)
                        raise

        def get_dehydrated_publishers(self):
                """Return the list of dehydrated publishers' prefixes."""

                return self._img.cfg.get_property("property", "dehydrated")


class Query(query_p.Query):
        """This class is the object used to pass queries into the api functions.
        It encapsulates the possible options available for a query as well as
        the text of the query itself."""

        def __init__(self, text, case_sensitive, return_actions=True,
            num_to_return=None, start_point=None):
                if return_actions:
                        return_type = query_p.Query.RETURN_ACTIONS
                else:
                        return_type = query_p.Query.RETURN_PACKAGES
                try:
                        query_p.Query.__init__(self, text, case_sensitive,
                            return_type, num_to_return, start_point)
                except query_p.QueryLengthExceeded as e:
                        raise apx.ParseError(e)


def get_default_image_root(orig_cwd=None):
        """Returns a tuple of (root, exact_match) where 'root' is the absolute
        path of the default image root based on current environment given the
        client working directory and platform defaults, and 'exact_match' is a
        boolean specifying how the default should be treated by ImageInterface.
        Note that the root returned may not actually be the valid root of an
        image; it is merely the default location a client should use when
        initializing an ImageInterface (e.g. '/' is not a valid image on Solaris
        10).

        The ImageInterface object will use the root provided as a starting point
        to find an image, searching upwards through each parent directory until
        '/' is reached based on the value of exact_match.

        'orig_cwd' should be the original current working directory at the time
        of client startup.  This value is assumed to be valid if provided,
        although permission and access errors will be gracefully handled.
        """

        # If an image location wasn't explicitly specified, check $PKG_IMAGE in
        # the environment.
        root = os.environ.get("PKG_IMAGE")
        exact_match = True
        if not root:
                if os.environ.get("PKG_FIND_IMAGE") or \
                    portable.osname != "sunos":
                        # If no image location was found in the environment,
                        # then see if user enabled finding image or if current
                        # platform isn't Solaris.  If so, attempt to find the
                        # image starting with the working directory.
                        root = orig_cwd
                        if root:
                                exact_match = False
                if not root:
                        # If no image directory has been determined based on
                        # request or environment, default to live root.
                        root = misc.liveroot()
        return root, exact_match


def image_create(pkg_client_name, version_id, root, imgtype, is_zone,
    cancel_state_callable=None, facets=misc.EmptyDict, force=False,
    mirrors=misc.EmptyI, origins=misc.EmptyI, prefix=None, refresh_allowed=True,
    repo_uri=None, ssl_cert=None, ssl_key=None, user_provided_dir=False,
    progtrack=None, variants=misc.EmptyDict, props=misc.EmptyDict,
    cmdpath=None):
        """Creates an image at the specified location.

        'pkg_client_name' is a string containing the name of the client,
        such as "pkg".

        'version_id' indicates the version of the api the client is
        expecting to use.

        'root' is the absolute path of the directory where the image will
        be created.  If it does not exist, it will be created.

        'imgtype' is an IMG_TYPE constant representing the type of image
        to create.

        'is_zone' is a boolean value indicating whether the image being
        created is for a zone.

        'cancel_state_callable' is an optional function reference that will
        be called if the cancellable status of an operation changes.

        'facets' is a dictionary of facet names and values to set during
        the image creation process.

        'force' is an optional boolean value indicating that if an image
        already exists at the specified 'root' that it should be overwritten.

        'mirrors' is an optional list of URI strings that should be added to
        all publishers configured during image creation as mirrors.

        'origins' is an optional list of URI strings that should be added to
        all publishers configured during image creation as origins.

        'prefix' is an optional publisher prefix to configure as a publisher
        for the new image if origins is provided, or to restrict which publisher
        will be configured if 'repo_uri' is provided.  If this prefix does not
        match the publisher configuration retrieved from the repository, an
        UnknownRepositoryPublishers exception will be raised.  If not provided,
        'refresh_allowed' cannot be False.

        'props' is an optional dictionary mapping image property names to values
        to be set while creating the image.

        'refresh_allowed' is an optional boolean value indicating whether
        publisher configuration data and metadata can be retrieved during
        image creation.  If False, 'repo_uri' cannot be specified and
        a 'prefix' must be provided.

        'repo_uri' is an optional URI string of a package repository to
        retrieve publisher configuration information from.  If the target
        repository supports this, all publishers found will be added to the
        image and any origins or mirrors will be added to all of those
        publishers.  If the target repository does not support this, and a
        prefix was not specified, an UnsupportedRepositoryOperation exception
        will be raised.  If the target repository supports the operation, but
        does not provide complete configuration information, a
        RepoPubConfigUnavailable exception will be raised.

        'ssl_cert' is an optional pathname of an SSL Certificate file to
        configure all publishers with and to use when retrieving publisher
        configuration information.  If provided, 'ssl_key' must also be
        provided.  The certificate file must be pem-encoded.

        'ssl_key' is an optional pathname of an SSL Key file to configure all
        publishers with and to use when retrieving publisher configuration
        information.  If provided, 'ssl_cert' must also be provided.  The
        key file must be pem-encoded.

        'user_provided_dir' is an optional boolean value indicating that the
        provided 'root' was user-supplied and that additional error handling
        should be enforced.  This primarily affects cases where a relative
        root has been provided or the root was based on the current working
        directory.

        'progtrack' is an optional ProgressTracker object.

        'variants' is a dictionary of variant names and values to set during
        the image creation process.

        Callers must provide one of the following when calling this function:
         * no 'prefix' and no 'origins'
         * a 'prefix' and 'repo_uri' (origins and mirrors are optional)
         * no 'prefix' and a 'repo_uri'  (origins and mirrors are optional)
         * a 'prefix' and 'origins'
        """

        # Caller must provide a prefix and repository, or no prefix and a
        # repository, or a prefix and origins, or no prefix and no origins.
        assert (prefix and repo_uri) or (not prefix and repo_uri) or (prefix and
            origins or (not prefix and not origins))

        # If prefix isn't provided and refresh isn't allowed, then auto-config
        # cannot be done.
        assert (prefix or refresh_allowed) or not repo_uri

        destroy_root = False
        try:
                destroy_root = not os.path.exists(root)
        except EnvironmentError as e:
                if e.errno == errno.EACCES:
                        raise apx.PermissionsException(
                            e.filename)
                raise

        # The image object must be created first since transport may be
        # needed to retrieve publisher configuration information.
        img = image.Image(root, force=force, imgtype=imgtype,
            progtrack=progtrack, should_exist=False,
            user_provided_dir=user_provided_dir, cmdpath=cmdpath,
            props=props)
        api_inst = ImageInterface(img, version_id,
            progtrack, cancel_state_callable, pkg_client_name,
            cmdpath=cmdpath)

        pubs = []

        try:
                if repo_uri:
                        # Assume auto configuration.
                        if ssl_cert:
                                try:
                                        misc.validate_ssl_cert(
                                            ssl_cert,
                                            prefix=prefix,
                                            uri=repo_uri)
                                except apx.ExpiringCertificate as e:
                                        logger.warning(e)

                        repo = publisher.RepositoryURI(repo_uri,
                            ssl_cert=ssl_cert, ssl_key=ssl_key)

                        pubs = None
                        try:
                                pubs = api_inst.get_publisherdata(repo=repo)
                        except apx.UnsupportedRepositoryOperation:
                                if not prefix:
                                        raise apx.RepoPubConfigUnavailable(
                                            location=repo_uri)
                                # For a v0 repo where a prefix was specified,
                                # fallback to manual configuration.
                                if not origins:
                                        origins = [repo_uri]
                                repo_uri = None

                        if not prefix and not pubs:
                                # Empty repository configuration.
                                raise apx.RepoPubConfigUnavailable(
                                    location=repo_uri)

                        if repo_uri:
                                for p in pubs:
                                        psrepo = p.repository
                                        if not psrepo:
                                                # Repository configuration info
                                                # was not provided, so assume
                                                # origin is repo_uri.
                                                p.repository = \
                                                    publisher.Repository(
                                                    origins=[repo_uri])
                                        elif not psrepo.origins:
                                                # Repository configuration was
                                                # provided, but without an
                                                # origin.  Assume the repo_uri
                                                # is the origin.
                                                psrepo.add_origin(repo_uri)
                                        elif repo not in psrepo.origins:
                                                # If the repo_uri used is not
                                                # in the list of sources, then
                                                # add it as the first origin.
                                                psrepo.origins.insert(0, repo)

                if prefix and not repo_uri:
                        # Auto-configuration not possible or not requested.
                        if ssl_cert:
                                try:
                                        misc.validate_ssl_cert(
                                            ssl_cert,
                                            prefix=prefix,
                                            uri=origins[0])
                                except apx.ExpiringCertificate as e:
                                        logger.warning(e)

                        repo = publisher.Repository()
                        for o in origins:
                                repo.add_origin(o) # pylint: disable=E1103
                        for m in mirrors:
                                repo.add_mirror(m) # pylint: disable=E1103
                        pub = publisher.Publisher(prefix,
                            repository=repo)
                        pubs = [pub]

                if prefix and prefix not in pubs:
                        # If publisher prefix requested isn't found in the list
                        # of publishers at this point, then configuration isn't
                        # possible.
                        known = [p.prefix for p in pubs]
                        raise apx.UnknownRepositoryPublishers(
                            known=known, unknown=[prefix], location=repo_uri)
                elif prefix:
                        # Filter out any publishers that weren't requested.
                        pubs = [
                            p for p in pubs
                            if p.prefix == prefix
                        ]

                # Add additional origins and mirrors that weren't found in the
                # publisher configuration if provided.
                for p in pubs:
                        pr = p.repository
                        for o in origins:
                                if not pr.has_origin(o):
                                        pr.add_origin(o)
                        for m in mirrors:
                                if not pr.has_mirror(m):
                                        pr.add_mirror(m)

                # Set provided SSL Cert/Key for all configured publishers.
                for p in pubs:
                        repo = p.repository
                        for o in repo.origins:
                                if o.scheme not in publisher.SSL_SCHEMES:
                                        continue
                                o.ssl_cert = ssl_cert
                                o.ssl_key = ssl_key
                        for m in repo.mirrors:
                                if m.scheme not in publisher.SSL_SCHEMES:
                                        continue
                                m.ssl_cert = ssl_cert
                                m.ssl_key = ssl_key

                img.create(pubs, facets=facets, is_zone=is_zone,
                    progtrack=progtrack, refresh_allowed=refresh_allowed,
                    variants=variants, props=props)
        except EnvironmentError as e:
                if e.errno == errno.EACCES:
                        raise apx.PermissionsException(
                            e.filename)
                if e.errno == errno.EROFS:
                        raise apx.ReadOnlyFileSystemException(
                            e.filename)
                raise
        except:
                # Ensure a defunct image isn't left behind.
                img.destroy()
                if destroy_root and \
                    os.path.abspath(root) != "/" and \
                    os.path.exists(root):
                        # Root didn't exist before create and isn't '/',
                        # so remove it.
                        shutil.rmtree(root, True)
                raise

        img.cleanup_downloads()

        return api_inst

# Vim hints
# vim:ts=8:sw=8:et:fdm=marker
