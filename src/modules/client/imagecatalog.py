#!/usr/bin/python3
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

#
# Copyright 2026 OmniOS Community Edition (OmniOSce) Association.
#

"""The image state database.

This module maintains a sqlite3 rendering of the image's 'known' and
'installed' catalogs (/var/pkg/state) in a single database file. The
installed catalogue is always a subset of the known catalogue whose entries
are identical, so installed state is a flag on the known rows rather
than a second copy.

The database provides indexed point lookups (the solver's access
pattern) and ordered streaming iteration (pkg list's access pattern)
without materialising entire catalogue parts in memory the way the JSON
"""

import os
import sqlite3
from urllib.parse import quote

import pkg.actions
import pkg.catalog
import pkg.client.api_errors as apx
import pkg.client.pkgdefs as pkgdefs
import pkg.fmri
import pkg.json_wrapper as json
import pkg.version
from pkg.misc import EmptyI

DB_BASENAME = "catalog.db"

# Bump this whenever the schema or the semantics of the stored data
# change; a mismatch makes the database unusable and it will be
# rebuilt.
SCHEMA_VERSION = 1

# Part identifiers for the actions table; these deliberately match
# pkg.catalog.Catalog.DEPENDENCY and pkg.catalog.Catalog.SUMMARY.
DEPENDENCY = pkg.catalog.Catalog.DEPENDENCY
SUMMARY = pkg.catalog.Catalog.SUMMARY

_BASE_PART = "catalog.base.C"
_DEPS_PART = "catalog.dependency.C"
_SUMM_PART_PFX = "catalog.summary."

_SCHEMA = [
    """CREATE TABLE meta (
        name  TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) WITHOUT ROWID""",
    """CREATE TABLE packages (
        id        INTEGER PRIMARY KEY,
        pub       TEXT NOT NULL,
        stem      TEXT NOT NULL,
        version   TEXT NOT NULL,
        sortkey   BLOB NOT NULL,
        installed INTEGER NOT NULL DEFAULT 0,
        base      TEXT NOT NULL,
        UNIQUE (pub, stem, version)
    )""",
    # One row per package part; 'acts' holds the part entry's action
    # strings joined with newlines. Action strings can never contain
    # a newline as manifests are line-oriented, and an empty list is
    # represented by the absence of a row (readers rely on this to
    # detect v0-era entries needing lazy manifest loading).
    """CREATE TABLE actions (
        pkg    INTEGER NOT NULL,
        part   INTEGER NOT NULL,
        locale TEXT NOT NULL,
        acts   TEXT NOT NULL,
        PRIMARY KEY (pkg, part, locale)
    ) WITHOUT ROWID""",
]

_INDEXES = [
    "CREATE INDEX pkg_stem ON packages (stem, sortkey)",
    "CREATE INDEX pkg_ordered ON packages (stem, pub, sortkey DESC)",
    "CREATE INDEX pkg_installed ON packages (stem) WHERE installed = 1",
]

# Batch size for row inserts.
_INSERT_BATCH = 5000


def summary_locales(cat):
    """Return the set of locales for which the given catalogue has
    summary parts."""

    try:
        names = cat._attrs.parts
    except AttributeError:
        # StateCatalog exposes part names directly.
        names = cat.parts
    return set(
        name[len(_SUMM_PART_PFX) :]
        for name in names
        if name.startswith(_SUMM_PART_PFX)
    )


def build_db(pathname, kcat, icat):
    """Create an image state database at 'pathname' from the 'known'
    catalogue 'kcat' and the 'installed' catalogue 'icat'.

    The caller provides crash-safety: the database is expected to be
    created inside a temporary state directory which is renamed into
    place as a whole, so no journal is used here."""

    if os.path.exists(pathname):
        os.unlink(pathname)

    con = sqlite3.connect(
        pathname, isolation_level=None, check_same_thread=False
    )
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("PRAGMA temp_store = MEMORY")
        con.execute("BEGIN")
        for ddl in _SCHEMA:
            con.execute(ddl)

        cur = con.cursor()

        # Packages and their base-part data. The same version string
        # recurs across many stems, so cache the computed sort keys.
        vcache = {}

        def sortkey(ver):
            try:
                return vcache[ver]
            except KeyError:
                sk = pkg.version.version_sortkey(ver)
                vcache[ver] = sk
                return sk

        ids = {}
        base = kcat.get_part(_BASE_PART, must_exist=True)
        if base is not None:
            for t, entry in base.tuple_entries():
                pub, stem, ver = t
                bentry = dict(
                    (k, v) for k, v in entry.items() if k != "version"
                )
                cur.execute(
                    "INSERT INTO packages"
                    " (pub, stem, version, sortkey, base)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (pub, stem, ver, sortkey(ver), json.dumps(bentry)),
                )
                ids[t] = cur.lastrowid

        def add_actions(part, ptype, locale):
            batch = []
            for t, entry in part.tuple_entries():
                pkg_id = ids.get(t)
                if pkg_id is None:
                    # No corresponding base entry; ignore, as
                    # the JSON implementation does.
                    continue
                acts = entry.get("actions", EmptyI)
                if not acts:
                    continue
                batch.append((pkg_id, ptype, locale, "\n".join(acts)))
                if len(batch) >= _INSERT_BATCH:
                    cur.executemany(
                        "INSERT INTO actions VALUES (?,?,?,?)",
                        batch,
                    )
                    batch = []
            if batch:
                cur.executemany("INSERT INTO actions VALUES (?,?,?,?)", batch)

        part = kcat.get_part(_DEPS_PART, must_exist=True)
        if part is not None:
            add_actions(part, DEPENDENCY, "C")
        for locale in summary_locales(kcat):
            part = kcat.get_part(_SUMM_PART_PFX + locale, must_exist=True)
            if part is not None:
                add_actions(part, SUMMARY, locale)

        # Installed state is a flag on the known entries.
        cur.executemany(
            "UPDATE packages SET installed = 1"
            " WHERE pub = ? AND stem = ? AND version = ?",
            icat.tuples(),
        )

        for ddl in _INDEXES:
            con.execute(ddl)

        lm = kcat.last_modified
        if lm is not None:
            lm = pkg.catalog.datetime_to_basic_ts(lm)
        cur.execute("INSERT INTO meta VALUES ('last-modified', ?)", (lm or "",))
        cur.execute("INSERT INTO meta VALUES ('complete', '1')")
        con.execute("PRAGMA user_version = {0:d}".format(SCHEMA_VERSION))
        con.execute("COMMIT")
    finally:
        con.close()


def _entry_action_rows(kcat, kdep, ksumm, pfmri, pkg_id):
    """Return the actions table rows for 'pfmri' drawn from the known
    catalog's dependency and summary parts."""

    rows = []
    if kdep is not None:
        entry = kdep.get_entry(pfmri=pfmri)
        if entry is not None:
            acts = entry.get("actions", EmptyI)
            if acts:
                rows.append((pkg_id, DEPENDENCY, "C", "\n".join(acts)))
    for locale, part in ksumm:
        if part is None:
            continue
        entry = part.get_entry(pfmri=pfmri)
        if entry is not None:
            acts = entry.get("actions", EmptyI)
            if acts:
                rows.append((pkg_id, SUMMARY, locale, "\n".join(acts)))
    return rows


def sync_entries(pathname, kcat, icat, pfmris):
    """Bring the database at 'pathname' up to date with the given
    known and installed catalogs following a state-changing operation
    that touched exactly the packages in 'pfmris'.

    Falls back to a full rebuild when the database is missing or
    unusable, or when the set of touched packages is unknown
    ('pfmris' is None).

    The caller provides crash-safety: as with build_db, the database
    is expected to be a copy inside a temporary state directory which
    is renamed into place as a whole."""

    db = ImageCatalog(pathname)
    usable = db.usable()
    db.close()
    if not usable or pfmris is None:
        build_db(pathname, kcat, icat)
        return

    con = sqlite3.connect(
        pathname, isolation_level=None, check_same_thread=False
    )
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("BEGIN")

        kdep = kcat.get_part(_DEPS_PART, must_exist=True)
        ksumm = [
            (locale, kcat.get_part(_SUMM_PART_PFX + locale, must_exist=True))
            for locale in summary_locales(kcat)
        ]

        for pfmri in pfmris:
            pub = pfmri.publisher
            stem = pfmri.pkg_name
            ver = str(pfmri.version)
            kentry = kcat.get_entry(pfmri)
            row = con.execute(
                "SELECT id FROM packages"
                " WHERE pub = ? AND stem = ? AND version = ?",
                (pub, stem, ver),
            ).fetchone()

            if kentry is None:
                # No longer known; drop it.
                if row:
                    con.execute("DELETE FROM actions WHERE pkg = ?", (row[0],))
                    con.execute("DELETE FROM packages WHERE id = ?", (row[0],))
                continue

            inst = int(icat.get_entry(pfmri) is not None)
            bjson = json.dumps(kentry)
            if row:
                pkg_id = row[0]
                con.execute(
                    "UPDATE packages SET base = ?, installed = ?"
                    " WHERE id = ?",
                    (bjson, inst, pkg_id),
                )
                con.execute("DELETE FROM actions WHERE pkg = ?", (pkg_id,))
            else:
                cur = con.execute(
                    "INSERT INTO packages"
                    " (pub, stem, version, sortkey, installed, base)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        pub,
                        stem,
                        ver,
                        pkg.version.version_sortkey(ver),
                        inst,
                        bjson,
                    ),
                )
                pkg_id = cur.lastrowid
            con.executemany(
                "INSERT INTO actions VALUES (?,?,?,?)",
                _entry_action_rows(kcat, kdep, ksumm, pfmri, pkg_id),
            )

        lm = kcat.last_modified
        if lm is not None:
            lm = pkg.catalog.datetime_to_basic_ts(lm)
        con.execute(
            "UPDATE meta SET value = ? WHERE name = 'last-modified'",
            (lm or "",),
        )
        con.execute("COMMIT")
    finally:
        con.close()


class ImageCatalog(object):
    """Read-only view over an image state database.

    With 'installed' set, the view is restricted to installed
    packages, mirroring the image's 'installed' catalog; otherwise it
    mirrors the 'known' catalog.

    Iteration and lookup results deliberately mirror the semantics of
    the corresponding pkg.catalog.Catalog methods: 'ordered' means
    sorted by stem, then publisher, then descending version;
    unordered iteration is ascending version order on a per-publisher,
    per-stem basis; 'last' selects the newest version per publisher
    and stem. Action data is returned as lists of action strings in
    catalogue part order (dependency data, then summary data by
    locale)."""

    def __init__(self, pathname, installed=False, manifest_cb=None):
        self.__path = pathname
        self.__installed = installed
        self.__con = None
        self.__manifest_cb = manifest_cb

    @property
    def pathname(self):
        return self.__path

    def close(self):
        if self.__con:
            try:
                self.__con.close()
            except sqlite3.Error:
                pass
            self.__con = None

    def __open(self):
        """Return a read-only connection, or None if the database
        does not exist or cannot be opened. The result is not cached
        in the None case as the database may appear later (a new
        image gains one on its first catalogue rebuild)."""

        if self.__con:
            return self.__con
        try:
            con = sqlite3.connect(
                "file:{0}?mode=ro".format(quote(self.__path)),
                uri=True,
                check_same_thread=False,
                isolation_level=None,
            )
            con.execute("PRAGMA temp_store = MEMORY")
            uv = con.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:
            return None
        if uv != SCHEMA_VERSION:
            # Not a database this code wrote (empty, truncated, or
            # foreign); treat it as absent so that reads see an
            # empty catalogue and the next rebuild replaces it.
            con.close()
            return None
        self.__con = con
        return con

    def _reader(self):
        """Return the read-only connection, or None if the database
        cannot be opened; for use by the in-package view classes."""

        return self.__open()

    def usable(self):
        """Return True if the database exists, is complete, and was
        written by a compatible version of this code."""

        try:
            con = self.__open()
            if con is None:
                return False
            uv = con.execute("PRAGMA user_version").fetchone()[0]
            if uv != SCHEMA_VERSION:
                return False
            meta = dict(con.execute("SELECT name, value FROM meta"))
        except (sqlite3.Error, EnvironmentError):
            return False
        return meta.get("complete") == "1"

    def last_modified(self):
        """The recorded last-modified timestamp (basic_ts format) of
        the known catalogue this database was built from, or None."""

        try:
            con = self.__open()
            if con is None:
                return None
            row = con.execute(
                "SELECT value FROM meta WHERE name = 'last-modified'"
            ).fetchone()
        except (sqlite3.Error, EnvironmentError):
            return None
        if row is None or row[0] == "":
            return None
        return row[0]

    def __where(self, pubs, prefix="WHERE", stems=None):
        """Return (sql-fragment, params) applying the view's installed
        restriction and optional publisher and stem restrictions."""

        conds = []
        params = []
        if self.__installed:
            conds.append("installed = 1")
        if pubs:
            conds.append("pub IN ({0})".format(",".join("?" * len(pubs))))
            params.extend(pubs)
        if stems is not None:
            if stems:
                conds.append("stem IN ({0})".format(",".join("?" * len(stems))))
                params.extend(sorted(stems))
            else:
                conds.append("0")
        if not conds:
            return "", params
        return " {0} {1}".format(prefix, " AND ".join(conds)), params

    def __select(self, cols, last, ordered, pubs, stems=None):
        """Return (sql, params) selecting 'cols' from packages with
        the standard iteration semantics applied."""

        where, params = self.__where(list(pubs), stems=stems)
        if last:
            sql = (
                "SELECT {0} FROM (SELECT *, row_number() OVER"
                " (PARTITION BY pub, stem ORDER BY sortkey DESC)"
                " AS rn FROM packages{1}) WHERE rn = 1".format(cols, where)
            )
        else:
            sql = "SELECT {0} FROM packages{1}".format(cols, where)
        if ordered:
            sql += " ORDER BY stem, pub, sortkey DESC"
        else:
            sql += " ORDER BY pub, stem, sortkey"
        return sql, params

    def tuples(self, last=False, ordered=False, pubs=EmptyI):
        """Yield (pub, stem, version) tuples."""

        con = self.__open()
        if con is None:
            return
        sql, params = self.__select("pub, stem, version", last, ordered, pubs)
        yield from con.execute(sql, params)

    def fmris(self, last=False, objects=True, ordered=False, pubs=EmptyI):
        """Yield FMRIs (PkgFmri objects, or strings with
        objects=False)."""

        for pub, stem, ver in self.tuples(
            last=last, ordered=ordered, pubs=pubs
        ):
            if objects:
                yield pkg.fmri.PkgFmri(name=stem, publisher=pub, version=ver)
            else:
                yield "pkg://{0}/{1}@{2}".format(pub, stem, ver)

    def fmris_by_version(self, name, pubs=EmptyI):
        """Yield (version, [fmri, ...]) tuples in ascending version
        order for the given package stem, matching
        pkg.catalog.Catalog.fmris_by_version: one tuple per distinct
        version string, with 'version' as a Version object."""

        con = self.__open()
        if con is None:
            return
        where, params = self.__where(list(pubs), prefix="AND")
        rows = con.execute(
            "SELECT version, pub FROM packages"
            " WHERE stem = ?{0}"
            " ORDER BY sortkey, version, pub".format(where),
            [name] + params,
        )
        cur_ver = None
        entries = []
        for ver, pub in rows:
            if cur_ver is not None and ver != cur_ver:
                yield pkg.version.Version(cur_ver), entries
                entries = []
            cur_ver = ver
            entries.append(
                pkg.fmri.PkgFmri(name=name, publisher=pub, version=ver)
            )
        if entries:
            yield pkg.version.Version(cur_ver), entries

    def names(self, pubs=EmptyI):
        """Return a set of the package stems in the view."""

        con = self.__open()
        if con is None:
            return set()
        where, params = self.__where(list(pubs))
        return set(
            r[0]
            for r in con.execute(
                "SELECT DISTINCT stem FROM packages{0}".format(where), params
            )
        )

    def pkg_names(self, pubs=EmptyI):
        """Yield (pub, stem) tuples sorted by stem then publisher."""

        con = self.__open()
        if con is None:
            return
        where, params = self.__where(list(pubs))
        yield from con.execute(
            "SELECT DISTINCT pub, stem FROM packages{0}"
            " ORDER BY stem, pub".format(where),
            params,
        )

    def publishers(self):
        """Return a set of the publisher prefixes in the view."""

        con = self.__open()
        if con is None:
            return set()
        where, params = self.__where([])
        return set(
            r[0]
            for r in con.execute(
                "SELECT DISTINCT pub FROM packages{0}".format(where), params
            )
        )

    def package_counts(self):
        """Return (package_count, package_version_count) matching the
        semantics of the JSON catalog's attrs values."""

        con = self.__open()
        if con is None:
            return 0, 0
        where, params = self.__where([])
        pkgs = con.execute(
            "SELECT count(DISTINCT pub || '!' || stem)"
            " FROM packages{0}".format(where),
            params,
        ).fetchone()[0]
        vers = con.execute(
            "SELECT count(*) FROM packages{0}".format(where), params
        ).fetchone()[0]
        return pkgs, vers

    def package_counts_by_pub(self, pubs=EmptyI):
        """Yield (pub, package_count, package_version_count) tuples,
        mirroring pkg.catalog.Catalog.get_package_counts_by_pub."""

        con = self.__open()
        if con is None:
            return
        where, params = self.__where(list(pubs))
        yield from con.execute(
            "SELECT pub, count(DISTINCT stem), count(*)"
            " FROM packages{0} GROUP BY pub".format(where),
            params,
        )

    def get_entry(self, pub=None, stem=None, ver=None, pfmri=None):
        """Return the base catalogue entry for the given package (minus
        its version key, as with the JSON implementation's merged
        entries), or None."""

        if pfmri is not None:
            pub, stem, ver = (
                pfmri.publisher,
                pfmri.pkg_name,
                str(pfmri.version),
            )
        con = self.__open()
        if con is None:
            return None
        where, params = self.__where([], prefix="AND")
        row = con.execute(
            "SELECT base FROM packages"
            " WHERE pub = ? AND stem = ? AND version = ?{0}".format(where),
            [pub, stem, ver] + params,
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def __parts_filter(self, info_needed, locales):
        """Return (sql-fragment, params) restricting the actions table
        to the requested parts and locales."""

        if not locales:
            locales = set(("C",))
        conds = []
        params = []
        if DEPENDENCY in info_needed:
            conds.append("(a.part = ? AND a.locale = 'C')")
            params.append(DEPENDENCY)
        if SUMMARY in info_needed:
            conds.append(
                "(a.part = ? AND a.locale IN ({0}))".format(
                    ",".join("?" * len(locales))
                )
            )
            params.append(SUMMARY)
            params.extend(sorted(locales))
        if not conds:
            return "0", params
        return " OR ".join(conds), params

    def get_entry_actions(self, t, info_needed, locales=None):
        """Return the list of action strings of the requested types
        for the package identified by tuple or FMRI 't', in catalog
        part order."""

        if isinstance(t, pkg.fmri.PkgFmri):
            pub, stem, ver = t.publisher, t.pkg_name, str(t.version)
        else:
            pub, stem, ver = t
        con = self.__open()
        if con is None:
            return []
        pf, pparams = self.__parts_filter(info_needed, locales)
        where, wparams = self.__where([], prefix="AND")
        astrs = []
        for r in con.execute(
            "SELECT a.acts FROM actions a"
            " JOIN packages p ON p.id = a.pkg"
            " WHERE p.pub = ? AND p.stem = ? AND p.version = ?{0}"
            " AND ({1})"
            " ORDER BY a.part, a.locale".format(where, pf),
            [pub, stem, ver] + wparams + pparams,
        ):
            astrs.extend(r[0].split("\n"))
        return astrs

    def entry_actions(
        self,
        info_needed,
        last=False,
        locales=None,
        ordered=False,
        pubs=EmptyI,
        stems=None,
    ):
        """Yield ((pub, stem, version), entry, [action string, ...])
        tuples, where 'entry' is the base catalogue entry. Mirrors
        pkg.catalog.Catalog.entry_actions, with action strings in
        place of the Action generator. 'stems' optionally restricts
        the results to the given set of package stems."""

        con = self.__open()
        if con is None:
            return
        pf, pparams = self.__parts_filter(info_needed, locales)
        sql, params = self.__select(
            "id, pub, stem, version, base", last, ordered, pubs, stems=stems
        )
        # Distinct cursors on one connection; the outer result set
        # streams while the inner per-package query runs.
        for pkg_id, pub, stem, ver, base in con.execute(sql, params):
            actions = []
            for r in con.execute(
                "SELECT acts FROM actions a"
                " WHERE a.pkg = ? AND ({0})"
                " ORDER BY a.part, a.locale".format(pf),
                [pkg_id] + pparams,
            ):
                actions.extend(r[0].split("\n"))
            yield (pub, stem, ver), json.loads(base), actions

    def gen_actions(self, t, info_needed, excludes=EmptyI, locales=None):
        """A generator function that produces Action objects for the
        requested information for the package identified by tuple or
        FMRI 't'; see _gen_action_objs for the semantics. Action
        data for entries originating from v0 catalogue sources is
        lazy-loaded from the package manifest when a manifest
        callback was provided."""

        astrs = self.get_entry_actions(t, info_needed, locales=locales)
        if not astrs:
            if isinstance(t, pkg.fmri.PkgFmri):
                f = t
                entry = self.get_entry(pfmri=t)
            else:
                f = pkg.fmri.PkgFmri(name=t[1], publisher=t[0], version=t[2])
                entry = self.get_entry(pub=t[0], stem=t[1], ver=t[2])
            lazy = None
            if entry is not None:
                lazy = self.lazy_actions(
                    f, entry, info_needed, locales, excludes
                )
            if lazy is not None:
                return lazy
        return _gen_action_objs(t, astrs, excludes)

    def lazy_actions(self, f, entry, info_needed, locales, excludes):
        """Return a generator of Actions lazy-loaded from the
        manifest of 'f', for entries originating from v0 catalog
        sources (which carry no action data in the catalog), or None
        when not applicable."""

        if self.__manifest_cb is None:
            return None
        states = entry.get("metadata", {}).get("states", ())
        if pkgdefs.PKG_STATE_V1 in states:
            # Entry has all of its action data in the catalog.
            return None
        m = self.__manifest_cb(f)
        if not m:
            return None
        if not locales:
            locales = set(("C",))
        return _gen_lazy_actions(m, info_needed, locales, excludes)

    def get_entry_all_variants(self, pfmri):
        """A generator function that yields tuples of the format
        (var_name, variants), mirroring
        pkg.catalog.Catalog.get_entry_all_variants."""

        if self.get_entry(pfmri=pfmri) is None:
            raise apx.UnknownCatalogEntry(pfmri.get_fmri())

        for a in self.gen_actions(pfmri, (DEPENDENCY,)):
            if a.name != "set":
                continue
            attr_name = a.attrs["name"]
            if not attr_name.startswith("variant"):
                continue
            yield attr_name, a.attrs["value"]


class SolverCatalog(object):
    """A drop-in for the subset of the pkg.catalog.Catalog interface
    used by pkg.client.pkg_solver.PkgSolver, backed by the image state
    database.

    Note that unlike the JSON implementation there is no manifest_cb
    lazy-loading fallback here: entries whose action data is not
    present in the catalogue (only possible for packages installed from
    long-obsolete v0 catalogue sources) yield no actions. Callers gate
    use of this class on the image state database being present and
    current."""

    def __init__(self, db):
        self.__db = db

    def fmris_by_version(self, name, pubs=EmptyI):
        return self.__db.fmris_by_version(name, pubs=pubs)

    def get_entry_actions(
        self, pfmri, info_needed, excludes=EmptyI, locales=None
    ):
        return self.__db.gen_actions(
            pfmri, info_needed, excludes=excludes, locales=locales
        )

    def get_entry_all_variants(self, pfmri):
        return self.__db.get_entry_all_variants(pfmri)


def _gen_action_objs(t, astrs, excludes):
    """A generator function producing Action objects from the action
    strings 'astrs' belonging to the package identified by tuple or
    FMRI 't', mirroring pkg.catalog.Catalog.__gen_actions: action
    strings that fail to parse are accumulated and raised as
    InvalidPackageErrors at the end, facet and variant 'set' actions
    bypass excludes filtering, and everything else is filtered
    through include_this."""

    if isinstance(t, pkg.fmri.PkgFmri):
        pub = t.publisher
    else:
        pub = t[0]

    errors = None
    for astr in astrs:
        try:
            a = pkg.actions.fromstr(astr)
        except pkg.actions.ActionError as e:
            if errors is None:
                errors = []
            if not isinstance(t, pkg.fmri.PkgFmri):
                t = pkg.fmri.PkgFmri(name=t[1], publisher=t[0], version=t[2])
            e.fmri = t
            errors.append(e)
            continue

        if a.name == "set" and (
            a.attrs["name"].startswith("facet")
            or a.attrs["name"].startswith("variant")
        ):
            # Don't filter actual facet or variant set actions.
            yield a
        elif a.include_this(excludes, publisher=pub):
            yield a

    if errors is not None:
        raise apx.InvalidPackageErrors(errors)


class ListCatalog(object):
    """A drop-in for the subset of the pkg.catalog.Catalog interface
    used by the package listing paths (pkg.client.api's __get_pkg_list
    and __map_installed_newest), backed by the image state database.

    As with SolverCatalog, there is no manifest_cb lazy-loading
    fallback; callers gate use of this class on the image state
    database being present and current."""

    # Callers reference these via catalogue instances.
    DEPENDENCY = DEPENDENCY
    SUMMARY = SUMMARY

    def __init__(self, db):
        self.__db = db

    def entry_actions(
        self,
        info_needed,
        excludes=EmptyI,
        cb=None,
        last=False,
        locales=None,
        ordered=False,
        pubs=EmptyI,
        stems=None,
    ):
        """Mirrors pkg.catalog.Catalog.entry_actions, yielding
        ((pub, stem, version), entry, actions) with 'actions' a
        generator of Action objects. 'cb' is invoked with the tuple
        and the entry before any action data is materialised.
        'stems' optionally restricts the results to the given set of
        package stems, pushed down into the database query."""

        for t, entry, astrs in self.__db.entry_actions(
            info_needed,
            last=last,
            locales=locales,
            ordered=ordered,
            pubs=pubs,
            stems=stems,
        ):
            if astrs:
                entry["actions"] = astrs
            if cb is not None and not cb(t, entry):
                continue
            if not astrs:
                # Entries from v0 catalogue sources have no cached
                # action data; lazy-load from the manifest when
                # possible.
                f = pkg.fmri.PkgFmri(name=t[1], publisher=t[0], version=t[2])
                lazy = self.__db.lazy_actions(
                    f, entry, info_needed, locales, excludes
                )
                if lazy is not None:
                    yield t, entry, lazy
                    continue
            yield t, entry, _gen_action_objs(t, astrs, excludes)

    def actions(
        self,
        info_needed,
        excludes=EmptyI,
        cb=None,
        last=False,
        locales=None,
        ordered=False,
        pubs=EmptyI,
    ):
        """Yield (fmri, actions-generator) tuples; mirrors
        pkg.catalog.Catalog.actions."""

        for t, entry, acts in self.entry_actions(
            info_needed,
            excludes=excludes,
            cb=cb,
            last=last,
            locales=locales,
            ordered=ordered,
            pubs=pubs,
        ):
            pub, stem, ver = t
            yield (
                pkg.fmri.PkgFmri(name=stem, publisher=pub, version=ver),
                acts,
            )

    def get_entry_actions(
        self, pfmri, info_needed, excludes=EmptyI, locales=None
    ):
        return self.__db.gen_actions(
            pfmri, info_needed, excludes=excludes, locales=locales
        )

    def names(self, pubs=EmptyI):
        return self.__db.names(pubs=pubs)

    def pkg_names(self, pubs=EmptyI):
        return self.__db.pkg_names(pubs=pubs)

    def publishers(self):
        return self.__db.publishers()

    @property
    def package_count(self):
        return self.__db.package_counts()[0]

    @property
    def package_version_count(self):
        return self.__db.package_counts()[1]


class StateCatalog(object):
    """A read-write facade over the image state database presenting
    the subset of the pkg.catalog.Catalog interface used by client
    code for the image 'known' and 'installed' catalogs on version 5
    images, where the database is the only store.

    Reads are served directly from the database. Writes follow the
    JSON implementation's usage pattern: get_entry hands out
    identity-stable entry dictionaries, mutations are recorded by
    update_entry/remove_package/append as pending operations, and the
    image applies them to a copy of the database via apply_pending
    inside its usual temporary-state-directory-and-rename flow.

    There is no manifest_cb lazy-loading fallback: entries originating
    from long-obsolete v0 catalogue sources yield no cached actions."""

    DEPENDENCY = DEPENDENCY
    SUMMARY = SUMMARY

    def __init__(self, pathname, installed=False, manifest_cb=None):
        self.__db = ImageCatalog(
            pathname, installed=installed, manifest_cb=manifest_cb
        )
        self.__lc = ListCatalog(self.__db)
        self.__installed = installed
        self.__ecache = {}
        self.__pending = []
        self.__parts_cache = None

    # -- infrastructure --------------------------------------------

    @property
    def pathname(self):
        return self.__db.pathname

    def close(self):
        self.__parts_cache = None
        self.__db.close()

    @property
    def batch_mode(self):
        return True

    @batch_mode.setter
    def batch_mode(self, value):
        pass

    @property
    def sign(self):
        return False

    @property
    def signatures(self):
        return {}

    @property
    def version(self):
        return 1

    @property
    def exists(self):
        return self.__db.usable()

    @property
    def meta_root(self):
        return os.path.dirname(self.__db.pathname)

    @meta_root.setter
    def meta_root(self, value):
        # The database location is managed by the image; state saves
        # operate on a copy via apply_pending instead of relocating
        # the catalog.
        pass

    @property
    def last_modified(self):
        ts = self.__db.last_modified()
        if ts is None:
            return None
        return pkg.catalog.basic_ts_to_datetime(ts)

    @property
    def created(self):
        return self.last_modified

    @property
    def package_count(self):
        return self.__db.package_counts()[0]

    @property
    def package_version_count(self):
        return self.__db.package_counts()[1]

    def finalize(self, pfmris=None, pubs=None):
        pass

    def save(self, fmt=None):
        # Persistence happens via Image.__catalog_save applying the
        # pending operations to a copy of the database.
        pass

    def destroy(self):
        self.close()
        try:
            os.unlink(self.__db.pathname)
        except OSError:
            pass

    # -- read side --------------------------------------------------

    @staticmethod
    def __tuple(pfmri=None, pub=None, stem=None, ver=None):
        if pfmri is not None:
            return (pfmri.publisher, pfmri.pkg_name, str(pfmri.version))
        return (pub, stem, ver)

    def tuples(self, last=False, ordered=False, pubs=EmptyI):
        return self.__db.tuples(last=last, ordered=ordered, pubs=pubs)

    def fmris(self, last=False, objects=True, ordered=False, pubs=EmptyI):
        return self.__db.fmris(
            last=last, objects=objects, ordered=ordered, pubs=pubs
        )

    def fmris_by_version(self, name, pubs=EmptyI):
        return self.__db.fmris_by_version(name, pubs=pubs)

    def names(self, pubs=EmptyI):
        return self.__db.names(pubs=pubs)

    def pkg_names(self, pubs=EmptyI):
        return self.__db.pkg_names(pubs=pubs)

    def publishers(self):
        return self.__db.publishers()

    def get_package_counts_by_pub(self, pubs=EmptyI):
        return self.__db.package_counts_by_pub(pubs=pubs)

    def entry_actions(self, info_needed, **kwargs):
        return self.__lc.entry_actions(info_needed, **kwargs)

    def actions(self, info_needed, **kwargs):
        """Yield (fmri, actions-generator) tuples; mirrors
        pkg.catalog.Catalog.actions."""

        return self.__lc.actions(info_needed, **kwargs)

    def entries(
        self,
        info_needed=EmptyI,
        last=False,
        locales=None,
        ordered=False,
        pubs=EmptyI,
    ):
        """Yield (fmri, entry) tuples with merged entries; mirrors
        pkg.catalog.Catalog.entries."""

        for t, entry, astrs in self.__db.entry_actions(
            info_needed,
            last=last,
            locales=locales,
            ordered=ordered,
            pubs=pubs,
        ):
            pub, stem, ver = t
            if astrs:
                entry["actions"] = astrs
            yield (
                pkg.fmri.PkgFmri(name=stem, publisher=pub, version=ver),
                entry,
            )

    def entries_by_version(
        self, name, info_needed=EmptyI, locales=None, pubs=EmptyI
    ):
        """Yield (version, [(fmri, entry), ...]) tuples in ascending
        version order; mirrors
        pkg.catalog.Catalog.entries_by_version."""

        entries = {}
        for t, entry, astrs in self.__db.entry_actions(
            info_needed, locales=locales, pubs=pubs, stems=set((name,))
        ):
            pub, stem, ver = t
            if astrs:
                entry["actions"] = astrs
            f = pkg.fmri.PkgFmri(name=stem, publisher=pub, version=ver)
            entries.setdefault(ver, []).append((f, entry))
        for sver in sorted(entries, key=pkg.version.version_sortkey):
            yield pkg.version.Version(sver), entries[sver]

    def tuple_entries(self, cb=None, last=False, ordered=False, pubs=EmptyI):
        """Yield ((pub, stem, version), entry) tuples for the base
        entries, including the version key, mirroring
        pkg.catalog.Catalog.tuple_entries."""

        for t, entry, astrs in self.__db.entry_actions(
            (), last=last, ordered=ordered, pubs=pubs
        ):
            if cb is None or cb(t, entry):
                yield t, entry

    def get_entry(self, pfmri=None, info_needed=EmptyI, locales=None):
        """Return the merged entry for the given package, or None.
        The dictionary values are identity-stable across calls so
        that the read-mutate-update_entry pattern used by
        Image.update_pkg_installed_state behaves as it does with the
        JSON implementation."""

        t = self.__tuple(pfmri=pfmri)
        try:
            base = self.__ecache[t]
        except KeyError:
            base = self.__db.get_entry(pfmri=pfmri)
            if base is None:
                return None
            self.__ecache[t] = base
        entry = dict(base)
        if info_needed:
            astrs = self.__db.get_entry_actions(t, info_needed, locales=locales)
            if astrs:
                entry["actions"] = astrs
        return entry

    def get_entry_actions(
        self, pfmri, info_needed, excludes=EmptyI, locales=None
    ):
        return self.__db.gen_actions(
            pfmri, info_needed, excludes=excludes, locales=locales
        )

    def get_entry_all_variants(self, pfmri):
        return self.__db.get_entry_all_variants(pfmri)

    def get_entry_variants(self, pfmri, name):
        for var_name, values in self.get_entry_all_variants(pfmri):
            if var_name == name:
                return values
        return None

    def get_entry_signatures(self, pfmri):
        entry = self.get_entry(pfmri=pfmri)
        if entry is None:
            raise apx.UnknownCatalogEntry(pfmri.get_fmri())
        return (
            (k.split("signature-")[1], v)
            for k, v in entry.items()
            if k.startswith("signature-")
        )

    def categories(self, excludes=EmptyI, pubs=EmptyI):
        """Return the set of (scheme, category) tuples used by the
        last version of each package; mirrors
        pkg.catalog.Catalog.categories."""

        return set(
            sc
            for t, entry, acts in self.__lc.entry_actions(
                (SUMMARY,), excludes=excludes, last=True, pubs=pubs
            )
            for a in acts
            if a.has_category_info()
            for sc in a.parse_category_info()
        )

    @property
    def parts(self):
        """A dictionary-alike (name keys) of the catalogue parts the
        database contents correspond to, mirroring the JSON
        implementation's attrs.parts keys for callers that iterate
        the image catalogs part by part."""

        if self.__parts_cache is None:
            names = [_BASE_PART]
            con = self.__db._reader()
            if con is None:
                # The database may yet appear; don't cache.
                return dict((n, {}) for n in names)
            for ptype, locale in con.execute(
                "SELECT DISTINCT part, locale FROM actions"
            ):
                if ptype == DEPENDENCY:
                    names.append(_DEPS_PART)
                else:
                    names.append(_SUMM_PART_PFX + locale)
            self.__parts_cache = names
        return dict((n, {}) for n in self.__parts_cache)

    def get_part(self, name, must_exist=False):
        """Return a read-only part view; mirrors
        pkg.catalog.Catalog.get_part for the read side."""

        if name == _BASE_PART:
            return _PartView(self.__db, name)
        if name == _DEPS_PART:
            pv = _PartView(self.__db, name, ptype=DEPENDENCY, locale="C")
        elif name.startswith(_SUMM_PART_PFX):
            pv = _PartView(
                self.__db,
                name,
                ptype=SUMMARY,
                locale=name[len(_SUMM_PART_PFX) :],
            )
        else:
            return None
        if must_exist and name not in self.parts:
            return None
        return pv

    # -- write side -------------------------------------------------

    @property
    def pending(self):
        """The recorded but not yet applied state changes."""
        return list(self.__pending)

    def update_entry(self, metadata, pfmri=None, pub=None, stem=None, ver=None):
        """Record new BASE record metadata for the given package;
        mirrors pkg.catalog.Catalog.update_entry."""

        t = self.__tuple(pfmri=pfmri, pub=pub, stem=stem, ver=ver)
        base = self.__ecache.get(t)
        if base is None:
            base = self.__db.get_entry(pub=t[0], stem=t[1], ver=t[2])
            if base is None:
                raise apx.UnknownCatalogEntry("/".join(t))
            self.__ecache[t] = base
        base["metadata"] = metadata
        self.__pending.append(("update", t, base))

    def remove_package(self, pfmri):
        """Record removal of the given package: for the installed
        view the installed flag is cleared; for the known view the
        entry is removed entirely."""

        t = self.__tuple(pfmri=pfmri)
        if self.__installed:
            self.__pending.append(("uninstall", t, None))
        else:
            self.__pending.append(("delete", t, None))
        self.__ecache.pop(t, None)

    def append(self, src, cb=None, pfmri=None, pubs=EmptyI):
        """Record that the given package (already present in the
        known rows of the shared database) is installed. Only the
        single-package form used by the image is supported."""

        assert self.__installed and pfmri is not None
        t = self.__tuple(pfmri=pfmri)
        self.__pending.append(("install", t, None))

    def apply_pending(self, con):
        """Apply recorded state changes to the database open on
        'con', clearing the pending list."""

        self.__parts_cache = None
        for op, t, base in self.__pending:
            if op == "update":
                bentry = dict((k, v) for k, v in base.items() if k != "version")
                con.execute(
                    "UPDATE packages SET base = ?"
                    " WHERE pub = ? AND stem = ? AND version = ?",
                    (json.dumps(bentry),) + t,
                )
            elif op == "install":
                con.execute(
                    "UPDATE packages SET installed = 1"
                    " WHERE pub = ? AND stem = ? AND version = ?",
                    t,
                )
            elif op == "uninstall":
                con.execute(
                    "UPDATE packages SET installed = 0"
                    " WHERE pub = ? AND stem = ? AND version = ?",
                    t,
                )
            elif op == "delete":
                row = con.execute(
                    "SELECT id FROM packages"
                    " WHERE pub = ? AND stem = ? AND version = ?",
                    t,
                ).fetchone()
                if row:
                    con.execute("DELETE FROM actions WHERE pkg = ?", (row[0],))
                    con.execute("DELETE FROM packages WHERE id = ?", (row[0],))
        self.__pending = []


class _PartView(object):
    """A read-only view over the state database presenting a single
    catalogue part, for callers (notably the image catalogue rebuild)
    that consume the image catalogs part by part."""

    def __init__(self, db, name, ptype=None, locale=None):
        self.__db = db
        self.__name = name
        self.__ptype = ptype
        self.__locale = locale

    @property
    def name(self):
        return self.__name

    def tuple_entries(self, cb=None, last=False, ordered=False, pubs=EmptyI):
        if self.__ptype is None:
            # Base part: entries include the version key.
            for t, entry, astrs in self.__db.entry_actions(
                (), last=last, ordered=ordered, pubs=pubs
            ):
                entry["version"] = t[2]
                if cb is None or cb(t, entry):
                    yield t, entry
            return
        info = (self.__ptype,)
        locales = None
        if self.__ptype == SUMMARY:
            locales = set((self.__locale,))
        for t, entry, astrs in self.__db.entry_actions(
            info, last=last, locales=locales, ordered=ordered, pubs=pubs
        ):
            if not astrs:
                # Package has no entry in this part.
                continue
            pentry = {"version": t[2], "actions": astrs}
            if cb is None or cb(t, pentry):
                yield t, pentry

    def entries(self, cb=None, last=False, ordered=False, pubs=EmptyI):
        """Yield (fmri, entry) tuples; mirrors
        pkg.catalog.CatalogPart.entries."""

        for t, entry in self.tuple_entries(
            last=last, ordered=ordered, pubs=pubs
        ):
            pub, stem, ver = t
            f = pkg.fmri.PkgFmri(name=stem, publisher=pub, version=ver)
            if cb is None or cb(f, entry):
                yield f, entry

    def get_entry(self, pfmri=None, pub=None, stem=None, ver=None):
        if pfmri is not None:
            pub, stem, ver = (
                pfmri.publisher,
                pfmri.pkg_name,
                str(pfmri.version),
            )
        t = (pub, stem, ver)
        if self.__ptype is None:
            entry = self.__db.get_entry(pub=pub, stem=stem, ver=ver)
            if entry is not None:
                entry["version"] = ver
            return entry
        locales = None
        if self.__ptype == SUMMARY:
            locales = set((self.__locale,))
        astrs = self.__db.get_entry_actions(t, (self.__ptype,), locales=locales)
        if not astrs:
            return None
        return {"version": ver, "actions": astrs}


def materialize(pathname, installed=False):
    """Return an in-memory pkg.catalog.Catalog populated from the
    state database at 'pathname', for callers that need to graft
    additional in-memory entries onto the image catalogs (alternate
    package sources)."""

    db = ImageCatalog(pathname, installed=installed)
    cat = pkg.catalog.Catalog(batch_mode=True, sign=False)
    try:
        base = cat.get_part(_BASE_PART)
        con = db._reader()
        if con is None:
            cat.finalize()
            return cat
        for t, entry, astrs in db.entry_actions(()):
            pub, stem, ver = t
            entry["version"] = ver
            base.add(
                metadata=entry,
                pub=pub,
                stem=stem,
                ver=ver,
                check_duplicate=False,
            )
        where = "WHERE p.installed = 1" if installed else ""
        rows = con.execute(
            "SELECT p.pub, p.stem, p.version, a.part, a.locale,"
            " a.acts FROM actions a"
            " JOIN packages p ON p.id = a.pkg {0}"
            " ORDER BY p.id, a.part, a.locale".format(where)
        )
        for pub, stem, ver, ptype, locale, acts in rows:
            if ptype == DEPENDENCY:
                name = _DEPS_PART
            else:
                name = _SUMM_PART_PFX + locale
            cat.get_part(name).add(
                metadata={"version": ver, "actions": acts.split("\n")},
                pub=pub,
                stem=stem,
                ver=ver,
                check_duplicate=False,
            )
        cat.finalize()
        # Reflect the database's state timestamp rather than the
        # materialisation time.
        ts = db.last_modified()
        if ts is not None:
            cat._attrs.last_modified = pkg.catalog.basic_ts_to_datetime(ts)
        return cat
    finally:
        db.close()


def apply_state_save(pathname, cats):
    """Apply the pending state changes recorded on the given
    StateCatalog objects, in order, to the database at 'pathname'
    (a copy inside a temporary state directory), and advance its
    last-modified timestamp."""

    con = sqlite3.connect(
        pathname, isolation_level=None, check_same_thread=False
    )
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        con.execute("BEGIN")
        for cat in cats:
            cat.apply_pending(con)
        con.execute(
            "UPDATE meta SET value = ? WHERE name = 'last-modified'",
            (pkg.catalog.now_to_basic_ts(),),
        )
        con.execute("COMMIT")
    finally:
        con.close()


def _gen_lazy_actions(m, info_needed, locales, excludes):
    """A generator function producing the Actions from manifest 'm'
    corresponding to the requested catalogue information, mirroring
    pkg.catalog.Catalog.__gen_lazy_actions (and, by extension,
    the group_actions logic used at publication)."""

    if DEPENDENCY in info_needed:
        atypes = ("depend", "set")
    elif SUMMARY in info_needed:
        atypes = ("set",)
    else:
        raise RuntimeError("Unknown info_needed type: {0}".format(info_needed))

    pub = m.publisher
    for atype in atypes:
        for a in m.gen_actions_by_type(atype):
            if not a.include_this(excludes, publisher=pub):
                continue
            attr_name = a.attrs.get("name", "")
            if (
                a.name == "depend"
                or attr_name.startswith("variant")
                or attr_name.startswith("facet")
                or attr_name.startswith("pkg.depend.")
                or attr_name in ("pkg.obsolete", "pkg.renamed")
            ):
                if DEPENDENCY in info_needed:
                    yield a
            elif SUMMARY in info_needed and a.name == "set":
                if attr_name in (
                    "fmri",
                    "pkg.fmri",
                    "publisher",
                ) or attr_name.startswith(
                    ("info.source-url", "pkg.debug", "pkg.linted")
                ):
                    continue
                comps = attr_name.split(":")
                if len(comps) > 1:
                    # 'set' is locale-specific.
                    if comps[1] not in locales:
                        continue
                yield a
