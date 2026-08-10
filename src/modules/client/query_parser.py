#!/usr/bin/python3
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
# Copyright (c) 2009, 2015, Oracle and/or its affiliates. All rights reserved.

import errno
import sys
import pkg.client.api_errors as api_errors
import pkg.client.searchdb as searchdb
import pkg.fmri as fmri
import pkg.manifest as manifest
from pkg.choose import choose

import pkg.query_parser as qp
from pkg.query_parser import (
    BooleanQueryException,
    ParseError,
    QueryLengthExceeded,
)


class QueryLexer(qp.QueryLexer):
    pass


class QueryParser(qp.QueryParser):
    """This class exists so that the classes the parent class query parser
    uses to build the AST are the ones defined in this module and not the
    parent class's module.  This is done so that a single query parser can
    be shared between the client and server modules but will construct an
    AST using the appropriate classes."""

    def __init__(self, lexer):
        qp.QueryParser.__init__(self, lexer)
        mod = sys.modules[QueryParser.__module__]
        tmp = {}
        for class_name in self.query_objs.keys():
            assert hasattr(mod, class_name)
            tmp[class_name] = getattr(mod, class_name)
        self.query_objs = tmp


# Because many classes do not have client specific modifications, they
# simply subclass the parent module's classes.
class Query(qp.Query):
    pass


class AndQuery(qp.AndQuery):
    def remove_root(self, img_dir):
        lcv = self.lc.remove_root(img_dir)
        rcv = self.rc.remove_root(img_dir)
        return lcv or rcv


class EmptyQuery(object):
    def __init__(self, return_type):
        self.return_type = return_type

    def search(self, *args):
        return []

    def set_info(self, **kwargs):
        return

    def __str__(self):
        if self.return_type == qp.Query.RETURN_ACTIONS:
            return "(a AND b)"
        else:
            return "<(a AND b)>"

    def propagate_pkg_return(self):
        """Makes this node return packages instead of actions.
        Returns None because no changes need to be made to the tree."""
        self.return_type = qp.Query.RETURN_PACKAGES
        return None


class OrQuery(qp.OrQuery):
    def remove_root(self, img_dir):
        lcv = self.lc.remove_root(img_dir)
        if not lcv:
            self.lc = EmptyQuery(self.lc.return_type)
        rcv = self.rc.remove_root(img_dir)
        if not rcv:
            self.rc = EmptyQuery(self.rc.return_type)
        return lcv or rcv


class PkgConversion(qp.PkgConversion):
    def remove_root(self, img_dir):
        return self.query.remove_root(img_dir)


class PhraseQuery(qp.PhraseQuery):
    def remove_root(self, img_dir):
        return self.query.remove_root(img_dir)


class FieldQuery(qp.FieldQuery):
    def remove_root(self, img_dir):
        return self.query.remove_root(img_dir)


class TopQuery(qp.TopQuery):
    """This class handles raising the exception if the search was conducted
    without using indexes.  It yields all results, then raises the
    exception."""

    def __init__(self, *args, **kwargs):
        qp.TopQuery.__init__(self, *args, **kwargs)
        self.__use_slow_search = False

    def get_use_slow_search(self):
        """Return whether slow search has been used."""

        return self.__use_slow_search

    def set_use_slow_search(self, val):
        """Set whether slow search has been used."""

        self.__use_slow_search = val

    def set_info(self, **kwargs):
        """This function provides the necessary information to the AST
        so that a search can be performed."""

        qp.TopQuery.set_info(
            self,
            get_use_slow_search=self.get_use_slow_search,
            set_use_slow_search=self.set_use_slow_search,
            **kwargs,
        )

    def search(self, *args):
        """This function performs performs local client side search.

        If slow search was used, then after all results have been
        returned, it raises SlowSearchUsed."""

        for i in qp.TopQuery.search(self, *args):
            yield i
        if self.__use_slow_search:
            raise api_errors.SlowSearchUsed()

    def remove_root(self, img_dir):
        return self.query.remove_root(img_dir)

    def add_or(self, rc):
        lc = self.query
        if isinstance(rc, TopQuery):
            rc = rc.query
        self.query = OrQuery(lc, rc)


class TermQuery(qp.TermQuery):
    """This class handles the client specific search logic for searching
    for a base query term. Searches are performed against the client
    search database rather than the flat-file index the depot server
    uses; the database is always complete, so no overlay of packages
    changed since the last index rebuild is needed."""

    def __init__(self, term):
        qp.TermQuery.__init__(self, term)
        self._impl_fmri_to_path = None
        self._efn = None
        self.__sdb = None

    def set_info(
        self,
        gen_installed_pkg_names,
        get_use_slow_search,
        set_use_slow_search,
        index_dir,
        get_manifest_path,
        case_sensitive,
        **kwargs,
    ):
        """This function provides the necessary information to the AST
        so that a search can be performed.

        The "gen_installed_pkg_names" parameter is a function which
        returns a generator function which iterates over the names of
        the installed packages in the image.

        The "get_use_slow_search" parameter is a function that returns
        whether slow search has been used.

        The "set_use_slow_search" parameter is a function that sets
        whether slow search was used."""

        self.get_use_slow_search = get_use_slow_search
        self._efn = gen_installed_pkg_names()
        self._dir_path = index_dir
        self._manifest_path_func = get_manifest_path
        self._case_sensitive = case_sensitive
        self.__sdb = searchdb.SearchDB(index_dir)
        # If no index is present, the slower version of search
        # will be used.
        set_use_slow_search(not self.__sdb.usable())

    def search(self, restriction, fmris, manifest_func, excludes):
        """This function performs performs local client side search.

        The "restriction" parameter is a generator over the results that
        another branch of the AST has already found.  If it exists,
        those results are treated as the domain for search.  If it does
        not exist, search uses the set of actions from installed
        packages as the domain.

        The "fmris" parameter is a function which produces an object
        which iterates over the names of installed fmris.

        The "manifest_func" parameter is a function which takes a fmri
        and returns a path to the manifest for that fmri.

        The "excludes" parameter is a list of the variants defined for
        this image."""

        if restriction:
            return self._restricted_search_internal(restriction)
        elif not self.get_use_slow_search():
            if self.__sdb.stored_hash() != searchdb.calc_hash(self._efn):
                raise api_errors.IncorrectIndexFileHash()
            return self._get_results(self.__db_search_internal())
        else:
            return self.slow_search(fmris, manifest_func, excludes)

    def __db_search_internal(self):
        """Generate (fmri string, [offset, ...], action type, key,
        displayed value) tuples for the occurrences matching this
        term from the search database, applying the same matching
        rules as the flat-file index: fnmatch globbing over tokens
        (forced, with case folding, when the search is
        case-insensitive), exact matching for the action type and
        key, and glob matching of the package name."""

        glob = self._glob
        term = self._term
        case_sensitive = self._case_sensitive

        if not case_sensitive:
            glob = True

        toks = None
        if glob:
            if self.has_non_wildcard_character.match(term):
                toks = choose(self.__sdb.tokens(), term, case_sensitive)
                if not toks:
                    return
        else:
            toks = [term]

        pkg_ids = None
        if not self.pkg_name_wildcard:
            pkg_ids = [
                pkg_id
                for pkg_id, fmri_str in self.__sdb.fmris()
                if self.pkg_name_match(fmri_str)
            ]
            if not pkg_ids:
                return

        atype = None if self.action_type_wildcard else self.action_type
        key = None if self.key_wildcard else self.key
        yield from self.__sdb.rows(toks, atype, key, pkg_ids)

    def _get_results(self, res):
        """Takes the results from the database search ("res") and
        reads the lines from the manifest files at the provided
        offsets. Unlike the shared implementation this opens each
        package's manifest only once, however many results it
        contributes, and tolerates a package whose manifest has gone
        missing from the image by skipping its results, so that a
        damaged image does not make search unusable."""

        res = list(res)
        wanted = {}
        for fmri_str, offsets, at, st, fv in res:
            wanted.setdefault(fmri_str, set()).update(offsets)

        lines = {}
        for fmri_str, offs in wanted.items():
            f = fmri.PkgFmri(fmri_str)
            try:
                path = self._manifest_path_func(f)
            except AssertionError:
                # The package's publisher cannot be resolved, so it
                # is no longer installed; skip the stale entry.
                continue
            try:
                file_handle = open(path, "rb", buffering=512)
            except EnvironmentError as e:
                if e.errno == errno.ENOENT:
                    continue
                raise
            for o in sorted(offs):
                file_handle.seek(o)
                lines[(fmri_str, o)] = file_handle.readline()
            file_handle.close()

        for fmri_str, offsets, at, st, fv in res:
            for o in sorted(offsets):
                l = lines.get((fmri_str, o))
                if l is not None:
                    yield at, st, fmri_str, fv, l

    def slow_search(self, fmris, manifest_func, excludes):
        """This function performs search when no prebuilt index is
        available.

        The "fmris" parameter is a generator function which iterates
        over the packages to be searched.

        The "manifest_func" parameter is a function which maps fmris to
        the path to their manifests.

        The "excludes" parameter is a list of variants defined in the
        image."""

        for pfmri in list(fmris()):
            fmri_str = pfmri.get_fmri(anarchy=True, include_scheme=False)
            if not (self.pkg_name_wildcard or self.pkg_name_match(fmri_str)):
                continue
            manf = manifest_func(pfmri)
            fast_update_dict = {}
            fast_update_res = []
            glob = self._glob
            term = self._term
            case_sensitive = self._case_sensitive

            if not case_sensitive:
                glob = True

            search_dict = manifest.Manifest.search_dict(
                manf, return_line=True, excludes=excludes
            )
            for tmp in search_dict:
                tok, at, st, fv = tmp
                if not (
                    self.action_type_wildcard or at == self.action_type
                ) or not (self.key_wildcard or st == self.key):
                    continue
                if tok not in fast_update_dict:
                    fast_update_dict[tok] = []
                fast_update_dict[tok].append(
                    (at, st, fv, fmri_str, search_dict[tmp])
                )
            if glob:
                keys = fast_update_dict.keys()
                matches = choose(keys, term, case_sensitive)
                fast_update_res = [fast_update_dict[m] for m in matches]
            else:
                if term in fast_update_dict:
                    fast_update_res.append(fast_update_dict[term])
            for sub_list in fast_update_res:
                for at, st, fv, fmri_str, line_list in sub_list:
                    for l in line_list:
                        yield at, st, fmri_str, fv, l

    def remove_root(self, img_root):
        if (
            (
                not self.action_type_wildcard
                and self.action_type != "file"
                and self.action_type != "link"
                and self.action_type != "hardlink"
                and self.action_type != "directory"
            )
            or (not self.key_wildcard and self.key != "path")
            or (not self._term.startswith(img_root) or img_root == "/")
        ):
            return False
        img_root = img_root.rstrip("/")
        self._term = self._term[len(img_root) :]
        self.key = "path"
        self.key_wildcard = False
        return True


# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
