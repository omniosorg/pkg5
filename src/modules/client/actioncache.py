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

"""The installed-action cache.

This module maintains a per-image sqlite3 database recording, for every
installed package, the stripped form of each globally-identical action
that the package delivers, keyed by action name and key attribute value,
together with a table of the key attribute values for which the installed
actions conflict with one another.

"""

import errno
import os
import sqlite3
from urllib.parse import quote

import pkg.actions
import pkg.client.imageplan as imageplan
import pkg.client.progress as progress
import pkg.misc as misc
import pkg.portable as portable

CACHE_BASENAME = "actions.sqlite"

SCHEMA_VERSION = 1

# The number of host variables used per statement when binding a large
# set of values. sqlite guarantees at least 999.
_CHUNK = 500

# Batch size for action row inserts.
_INSERT_BATCH = 5000

_SCHEMA = [
    """CREATE TABLE meta (
        name  TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) WITHOUT ROWID""",
    """CREATE TABLE packages (
        pkg_id INTEGER PRIMARY KEY,
        fmri   TEXT NOT NULL UNIQUE
    )""",
    """CREATE TABLE actions (
        pkg_id INTEGER NOT NULL,
        aname  TEXT NOT NULL,
        keyval TEXT NOT NULL,
        act    TEXT NOT NULL
    )""",
    """CREATE TABLE conflicts (
        ns     TEXT NOT NULL,
        keyval TEXT NOT NULL,
        PRIMARY KEY (ns, keyval)
    ) WITHOUT ROWID""",
]

_INDICES = [
    "CREATE INDEX actions_ix_key ON actions (keyval, aname)",
    "CREATE INDEX actions_ix_pkg ON actions (pkg_id)",
]


class ActionCacheError(Exception):
    """Base exception for installed-action cache errors."""


class ReadOnlyCacheError(ActionCacheError):
    """Raised when the database cannot be opened for writing."""


def _conflict_groups():
    """Return a dictionary mapping each namespace group that contains
    at least one globally-identical action type to the set of
    globally-identical action names within that group.

    Namespace group values themselves are not stable across releases
    (see pkg.actions.generic.NSG), which is why they are always derived
    at runtime and never stored in the database."""

    groups = {}
    for name, klass in pkg.actions.types.items():
        if not klass.globally_identical:
            continue
        groups.setdefault(klass.namespace_group, set()).add(name)
    return groups


def _excludes_signature(image):
    """Return a canonical string describing the variants and facets
    currently configured for 'image'. When this changes, the set of
    actions admitted by the image's excludes may change for any
    installed package, so the cache must be rebuilt in full."""

    return str(
        (
            sorted((str(k), str(v)) for k, v in image.cfg.variants.items()),
            sorted((str(k), str(v)) for k, v in image.cfg.facets.items()),
        )
    )


class ActionCache(object):
    """Manages the installed-action cache database for an image.

    All write operations assume the caller holds the image lock; the
    only concurrency handled here is unprivileged readers observing a
    database replaced by rename, which is safe because an open file
    descriptor keeps the old copy alive."""

    def __init__(self, image, cache_dir):
        self.__image = image
        self.__dir = cache_dir
        self.__path = os.path.join(cache_dir, CACHE_BASENAME)
        self.__con = None
        self.__rw = False
        self.__conid = None

    @property
    def cache_dir(self):
        return self.__dir

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
            self.__rw = False
            self.__conid = None

    def __fileid(self):
        """Return an identity token for the file currently at the
        database path, or None if it does not exist."""

        try:
            st = os.stat(self.__path)
            return (st.st_dev, st.st_ino)
        except EnvironmentError:
            return None

    def __check_replaced(self):
        """Drop the cached connection if the file at the database
        path is no longer the file the connection was opened on (a
        full rebuild replaces the database by rename)."""

        if self.__con and self.__conid != self.__fileid():
            self.close()

    def __connect(self, mode):
        con = sqlite3.connect(
            "file:{0}?mode={1}".format(quote(self.__path), mode),
            uri=True,
            check_same_thread=False,
            isolation_level=None,
        )
        con.execute("PRAGMA temp_store = MEMORY")
        return con

    def __open_ro(self):
        """Return a read-only connection to the database, or None if
        it does not exist or cannot be opened."""

        self.__check_replaced()
        if self.__con:
            return self.__con
        try:
            self.__con = self.__connect("ro")
        except sqlite3.OperationalError:
            return None
        self.__rw = False
        self.__conid = self.__fileid()
        return self.__con

    def __open_rw(self):
        """Return a read-write connection to the database, raising
        ReadOnlyCacheError if it exists but cannot be written."""

        self.__check_replaced()
        if self.__con and self.__rw:
            return self.__con
        self.close()
        # Verify writability of both the database file and its
        # directory (needed for the rollback journal) up front, so
        # that callers see a single exception type for the fallback
        # path; sqlite otherwise reports failures only at the first
        # actual write.
        try:
            fd = os.open(self.__path, os.O_WRONLY)
            os.close(fd)
            probe = self.__path + ".tmp"
            fd = os.open(
                probe,
                os.O_CREAT | os.O_WRONLY,
                misc.PKG_FILE_MODE,
            )
            os.close(fd)
            os.unlink(probe)
        except EnvironmentError as e:
            if e.errno in (errno.EACCES, errno.EROFS, errno.EPERM):
                raise ReadOnlyCacheError(str(e))
            raise
        try:
            con = self.__connect("rw")
        except sqlite3.OperationalError as e:
            raise ReadOnlyCacheError(str(e))
        con.execute("PRAGMA synchronous = NORMAL")
        self.__con = con
        self.__rw = True
        self.__conid = self.__fileid()
        return con

    def __installed_pfmris(self):
        """Return a dictionary mapping installed package fmri strings
        to their PkgFmri objects according to the image's installed
        catalog."""

        return dict(
            (str(pfmri), pfmri) for pfmri in self.__image.gen_installed_pkgs()
        )

    def __usable(self, con):
        """Return True if the database was completely built by a
        compatible version of this code for the image's current
        variants and facets."""

        try:
            uv = con.execute("PRAGMA user_version").fetchone()[0]
            if uv != SCHEMA_VERSION:
                return False
            meta = dict(con.execute("SELECT name, value FROM meta"))
        except sqlite3.DatabaseError:
            return False
        if meta.get("complete") != "1":
            return False
        return meta.get("excludes") == _excludes_signature(self.__image)

    def is_fresh(self):
        """Return True if the database exists, is usable, and is
        consistent with the image's installed catalog."""

        con = self.__open_ro()
        if con is None:
            return False
        try:
            if not self.__usable(con):
                return False
            dbf = set(r[0] for r in con.execute("SELECT fmri FROM packages"))
        except sqlite3.DatabaseError:
            return False
        return dbf == set(self.__installed_pfmris())

    def update(self, progtrack=None):
        """Bring the database into line with the installed catalog,
        incrementally where possible, rebuilding otherwise. Raises
        ReadOnlyCacheError or EnvironmentError (EACCES/EROFS) when the
        cache is not writable."""

        if not progtrack:
            progtrack = progress.NullProgressTracker()

        if not os.path.exists(self.__path):
            self.rebuild(progtrack=progtrack)
            return

        try:
            con = self.__open_rw()
            if not self.__usable(con):
                self.rebuild(progtrack=progtrack)
                return
            dbf = dict(con.execute("SELECT fmri, pkg_id FROM packages"))
        except sqlite3.DatabaseError:
            # Corrupt database; replace it. A cache that cannot be
            # written raises ReadOnlyCacheError instead, which is not
            # handled here.
            self.rebuild(progtrack=progtrack)
            return

        inst = self.__installed_pfmris()
        extra = [pkg_id for f, pkg_id in dbf.items() if f not in inst]
        missing = [f for f in inst if f not in dbf]
        if not extra and not missing:
            return

        progtrack.job_start(progtrack.JOB_FAST_LOOKUP)
        try:
            # (aname -> set(keyval)) touched by this update; conflict
            # state is recomputed for these keys.
            affected = {}
            cur = con.cursor()
            cur.execute("BEGIN IMMEDIATE")
            for i in range(0, len(extra), _CHUNK):
                chunk = extra[i : i + _CHUNK]
                qs = ",".join("?" * len(chunk))
                for aname, keyval in cur.execute(
                    "SELECT DISTINCT aname, keyval FROM actions"
                    " WHERE pkg_id IN ({0})".format(qs),
                    chunk,
                ).fetchall():
                    affected.setdefault(aname, set()).add(keyval)
                cur.execute(
                    "DELETE FROM actions WHERE pkg_id IN ({0})".format(qs),
                    chunk,
                )
                cur.execute(
                    "DELETE FROM packages WHERE pkg_id IN ({0})".format(qs),
                    chunk,
                )
                progtrack.job_add_progress(progtrack.JOB_FAST_LOOKUP)

            for f in missing:
                progtrack.job_add_progress(progtrack.JOB_FAST_LOOKUP)
                cur.execute("INSERT INTO packages (fmri) VALUES (?)", (f,))
                pkg_id = cur.lastrowid
                batch = []
                for _f, aname, keyval, act in self.__gen_package_rows(inst[f]):
                    affected.setdefault(aname, set()).add(keyval)
                    batch.append((pkg_id, aname, keyval, act))
                cur.executemany("INSERT INTO actions VALUES (?,?,?,?)", batch)

            self.__refresh_conflicts(con, progtrack, affected)
            cur.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        finally:
            progtrack.job_done(progtrack.JOB_FAST_LOOKUP)

    def __gen_package_rows(self, pfmri):
        """Yield (fmri string, action name, key attribute value,
        stripped action string) for every globally-identical action
        delivered by 'pfmri' under the image's current excludes."""

        excludes = self.__image.list_excludes()
        m = self.__image.get_manifest(pfmri, ignore_excludes=True)
        f = str(pfmri)
        for act in m.gen_actions(excludes=excludes):
            if not act.globally_identical:
                continue
            act.strip()
            yield f, act.name, act.attrs[act.key_attr], str(act)

    def rebuild(self, progtrack=None):
        """Rebuild the database from scratch from the manifests of the
        installed packages, atomically replacing any existing
        database. Raises EnvironmentError with EACCES/EROFS when the
        cache directory is not writable."""

        if not progtrack:
            progtrack = progress.NullProgressTracker()

        self.close()

        if not os.path.exists(self.__dir):
            os.makedirs(self.__dir)

        tmp_path = self.__path + ".tmp"
        # Probe writability with a plain open so that permission
        # problems surface as EnvironmentError with a meaningful errno
        # rather than a generic sqlite error, and remove any leftover
        # temporary database from an interrupted build.
        fd = os.open(
            tmp_path,
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            misc.PKG_FILE_MODE,
        )
        os.close(fd)

        progtrack.job_start(progtrack.JOB_FAST_LOOKUP)
        con = sqlite3.connect(
            tmp_path, check_same_thread=False, isolation_level=None
        )
        try:
            # Durability is provided by the rename into place below; a
            # partially-written temporary file is never visible.
            con.execute("PRAGMA journal_mode = OFF")
            con.execute("PRAGMA synchronous = OFF")
            con.execute("PRAGMA temp_store = MEMORY")
            con.execute("BEGIN")
            for ddl in _SCHEMA:
                con.execute(ddl)

            cur = con.cursor()
            batch = []
            for f, pfmri in self.__installed_pfmris().items():
                progtrack.job_add_progress(progtrack.JOB_FAST_LOOKUP)
                cur.execute("INSERT INTO packages (fmri) VALUES (?)", (f,))
                pkg_id = cur.lastrowid
                for _f, aname, keyval, act in self.__gen_package_rows(pfmri):
                    batch.append((pkg_id, aname, keyval, act))
                    if len(batch) >= _INSERT_BATCH:
                        cur.executemany(
                            "INSERT INTO actions VALUES (?,?,?,?)", batch
                        )
                        batch = []
            if batch:
                cur.executemany("INSERT INTO actions VALUES (?,?,?,?)", batch)

            progtrack.job_add_progress(progtrack.JOB_FAST_LOOKUP)
            for ddl in _INDICES:
                con.execute(ddl)

            self.__refresh_conflicts(con, progtrack, None)

            con.execute(
                "INSERT INTO meta VALUES ('excludes', ?)",
                (_excludes_signature(self.__image),),
            )
            con.execute("INSERT INTO meta VALUES ('complete', '1')")
            con.execute("PRAGMA user_version = {0:d}".format(SCHEMA_VERSION))
            con.execute("COMMIT")
            con.close()
            con = None
            os.chmod(tmp_path, misc.PKG_FILE_MODE)
            portable.rename(tmp_path, self.__path)
        except BaseException:
            if con:
                con.close()
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        finally:
            progtrack.job_done(progtrack.JOB_FAST_LOOKUP)

    def __refresh_conflicts(self, con, progtrack, affected):
        """Recompute the conflicts table.

        If 'affected' is None the whole table is rebuilt from the
        actions table; otherwise it is a dictionary mapping action
        names to the sets of key attribute values whose conflict state
        must be re-evaluated."""

        full = affected is None
        if full:
            con.execute("DELETE FROM conflicts")

        for ns, names in _conflict_groups().items():
            # The namespace group value itself is unstable across
            # releases, so conflicts rows are keyed by the smallest
            # action name in the group instead.
            rep = min(names)
            onames = sorted(names)
            nq = ",".join("?" * len(onames))
            # Refcountable, globally-identical types (dir, link,
            # hardlink, ...) may be delivered by any number of
            # packages provided the actions are identical, so groups
            # that are homogeneous in both type and content are
            # provably conflict-free and are filtered out in SQL
            # before any action parsing happens.
            refgi = sorted(
                n for n in names if pkg.actions.types[n].refcountable
            )
            rq = ",".join("?" * len(refgi))

            if full:
                keyvals = None
            else:
                keyvals = set()
                for aname in names:
                    keyvals |= affected.get(aname, set())
                if not keyvals:
                    continue
                keyvals = sorted(keyvals)

            bad = set()
            for kchunk in self.__chunks(keyvals):
                progtrack.job_add_progress(progtrack.JOB_FAST_LOOKUP)
                where = "aname IN ({0})".format(nq)
                params = list(onames)
                if kchunk is not None:
                    where += " AND keyval IN ({0})".format(
                        ",".join("?" * len(kchunk))
                    )
                    params += kchunk
                cands = [
                    r[0]
                    for r in con.execute(
                        "SELECT keyval FROM actions"
                        " WHERE {0} GROUP BY keyval"
                        " HAVING COUNT(*) > 1 AND NOT"
                        " (COUNT(DISTINCT aname) = 1"
                        "  AND COUNT(DISTINCT act) = 1"
                        "  AND MIN(aname) IN ({1}))".format(where, rq),
                        params + refgi,
                    )
                ]

                for cchunk in self.__chunks(cands, none_ok=False):
                    groups = {}
                    for keyval, act, f in con.execute(
                        "SELECT a.keyval, a.act, p.fmri"
                        " FROM actions a"
                        " JOIN packages p ON p.pkg_id = a.pkg_id"
                        " WHERE a.aname IN ({0})"
                        " AND a.keyval IN ({1})".format(
                            nq, ",".join("?" * len(cchunk))
                        ),
                        onames + cchunk,
                    ):
                        groups.setdefault(keyval, []).append(
                            (pkg.actions.fromstr(act), f)
                        )
                    for keyval, actions in groups.items():
                        if imageplan.ImagePlan._check_action_group(ns, actions):
                            bad.add(keyval)

                if kchunk is not None:
                    con.execute(
                        "DELETE FROM conflicts WHERE ns = ?"
                        " AND keyval IN ({0})".format(
                            ",".join("?" * len(kchunk))
                        ),
                        [rep] + kchunk,
                    )
            con.executemany(
                "INSERT OR REPLACE INTO conflicts VALUES (?, ?)",
                [(rep, k) for k in sorted(bad)],
            )

    @staticmethod
    def __chunks(vals, none_ok=True):
        """Split 'vals' into lists of at most _CHUNK items. If 'vals'
        is None and 'none_ok' is set, yield a single None, meaning 'no
        restriction'."""

        if vals is None:
            if none_ok:
                yield None
            return
        for i in range(0, len(vals), _CHUNK):
            yield vals[i : i + _CHUNK]

    def get_actions_by_aname(self, anames):
        """Yield (action name, fmri string, key attribute value,
        stripped action string) tuples for every cached action whose
        action name is in 'anames'."""

        con = self.__open_ro()
        if con is None:
            raise ActionCacheError(
                "installed-action cache disappeared: {0}".format(self.__path)
            )
        anames = sorted(anames)
        yield from con.execute(
            "SELECT a.aname, p.fmri, a.keyval, a.act"
            " FROM actions a"
            " JOIN packages p ON p.pkg_id = a.pkg_id"
            " WHERE a.aname IN ({0})"
            " ORDER BY a.aname, p.fmri".format(",".join("?" * len(anames))),
            anames,
        )

    def get_actions(self, anames, keys):
        """Yield (key attribute value, fmri string, stripped action
        string) tuples for every cached action whose action name is in
        'anames' and whose key attribute value is in 'keys'."""

        con = self.__open_ro()
        if con is None:
            raise ActionCacheError(
                "installed-action cache disappeared: {0}".format(self.__path)
            )
        anames = sorted(anames)
        nq = ",".join("?" * len(anames))
        keys = list(keys)
        for i in range(0, len(keys), _CHUNK):
            chunk = keys[i : i + _CHUNK]
            yield from con.execute(
                "SELECT a.keyval, p.fmri, a.act"
                " FROM actions a"
                " JOIN packages p ON p.pkg_id = a.pkg_id"
                " WHERE a.keyval IN ({0}) AND a.aname IN ({1})"
                " ORDER BY a.keyval, a.aname, p.fmri, a.act".format(
                    ",".join("?" * len(chunk)), nq
                ),
                chunk + anames,
            )

    def has_conflicts(self):
        """Return True if any installed actions conflict with each
        other (or if that cannot be determined)."""

        con = self.__open_ro()
        if con is None:
            return True
        try:
            return (
                con.execute(
                    "SELECT EXISTS (SELECT 1 FROM conflicts)"
                ).fetchone()[0]
                == 1
            )
        except sqlite3.DatabaseError:
            return True

    def conflicting_keys(self):
        """Return the set of key attribute values for which installed
        actions conflict, or None if the database is unusable."""

        con = self.__open_ro()
        if con is None:
            return None
        try:
            return set(
                r[0] for r in con.execute("SELECT keyval FROM conflicts")
            )
        except sqlite3.DatabaseError:
            return None

    def copy_to(self, cache_dir):
        """Return a new ActionCache rooted at 'cache_dir', seeded with
        a copy of this cache's database if one exists. Used to give
        unprivileged users a reconcilable private copy."""

        other = ActionCache(self.__image, cache_dir)
        try:
            misc.copyfile(self.__path, other.pathname)
            os.chmod(other.pathname, misc.PKG_FILE_MODE)
        except EnvironmentError as e:
            if e.errno != errno.ENOENT:
                raise
        return other

    def __dumpdb(self):
        """Return the comparable content of the database as a tuple
        of (action rows, conflict rows) frozensets."""

        con = self.__open_ro()
        if con is None:
            raise ActionCacheError(
                "installed-action cache disappeared: {0}".format(self.__path)
            )
        return (
            frozenset(
                con.execute(
                    "SELECT p.fmri, a.aname, a.keyval, a.act"
                    " FROM actions a"
                    " JOIN packages p ON p.pkg_id = a.pkg_id"
                )
            ),
            frozenset(con.execute("SELECT ns, keyval FROM conflicts")),
        )

    def selfcheck(self):
        """Compare the database against one rebuilt from scratch from
        the installed manifests, returning None if they are identical
        or a string describing the differences. This is the backend
        for the -D actioncache-verify=1 debug feature, used to
        validate incremental maintenance."""

        ref = ActionCache(self.__image, self.__image.temporary_dir())
        try:
            ref.rebuild()
            rows, conf = self.__dumpdb()
            rrows, rconf = ref.__dumpdb()
        finally:
            ref.close()
            try:
                os.unlink(ref.pathname)
            except OSError:
                pass

        if rows == rrows and conf == rconf:
            return None

        def describe(name, mine, theirs):
            missing = theirs - mine
            extra = mine - theirs
            out = []
            if missing:
                out.append(
                    "{0:d} missing {1} row(s), e.g. {2}".format(
                        len(missing), name, sorted(missing)[0]
                    )
                )
            if extra:
                out.append(
                    "{0:d} unexpected {1} row(s), e.g. {2}".format(
                        len(extra), name, sorted(extra)[0]
                    )
                )
            return out

        return "; ".join(
            describe("action", rows, rrows) + describe("conflict", conf, rconf)
        )
