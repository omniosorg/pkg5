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

"""The client search database.

This module maintains a sqlite3 rendering of the client search index
in a single database file, replacing the flat-file inverted index for
local images. Each row of the occurrences table records that a search
token was produced by an action of a given type and key, along with
the value to display for a match and the byte offsets of the
generating lines within the package's manifest; the query side reads
the matched action text back from the manifest, exactly as the
flat-file index did.

Unlike the flat-file index, updates are genuinely incremental: the
rows for changed packages are replaced individually, so there is no
threshold beyond which the whole index must be rebuilt. The depot
server continues to use the flat-file implementation for repository
search indexes."""

import errno
import hashlib
import os
import shutil
import sqlite3

import pkg.client.progress as progress
import pkg.manifest
import pkg.portable as portable
import pkg.search_errors as search_errors
from pkg.misc import EmptyI, force_bytes

DB_BASENAME = "search.db"

# Bump this whenever the schema or the semantics of the stored data
# change; a mismatch makes the database unusable and the index will
# need to be rebuilt.
SCHEMA_VERSION = 1

_SCHEMA = [
    """CREATE TABLE meta (
        name  TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) WITHOUT ROWID""",
    """CREATE TABLE pkgs (
        id   INTEGER PRIMARY KEY,
        fmri TEXT NOT NULL UNIQUE
    )""",
    # One row per (token, action type, key, displayed value, package);
    # 'offs' holds the space-separated byte offsets of the generating
    # action lines within the package's manifest file.
    """CREATE TABLE occ (
        pkg   INTEGER NOT NULL,
        tok   TEXT NOT NULL,
        atype TEXT NOT NULL,
        key   TEXT NOT NULL,
        fval  TEXT NOT NULL,
        offs  TEXT NOT NULL
    )""",
]

_INDEXES = [
    "CREATE INDEX occ_tok ON occ (tok)",
    "CREATE INDEX occ_pkg ON occ (pkg)",
]

# Files and directories belonging to the flat-file index format,
# removed whenever the database is (re)built so that older clients
# find no index rather than a stale one.
_LEGACY_FILES = [
    "main_dict.ascii.v2",
    "token_byte_offset.v1",
    "manf_list.v1",
    "fmri_offsets.v1",
    "fast_add.v1",
    "fast_remove.v1",
    "full_fmri_list",
    "full_fmri_list.hash",
    "lock",
]
_LEGACY_DIRS = ["pkg", "TMP"]

_INSERT_BATCH = 5000


def calc_hash(vals):
    """Calculate the hash of the sorted members of 'vals', matching
    the flat-file index's full-fmri-list hash computation."""

    vl = sorted(vals)
    shasum = hashlib.sha1()
    for v in vl:
        shasum.update(force_bytes(v))
    return shasum.hexdigest()


def _fmri_str(pfmri):
    """The canonical string form under which packages are recorded."""

    return pfmri.get_fmri(anarchy=True, include_scheme=False)


def _clear_legacy(index_dir):
    """Remove any flat-file format index from 'index_dir'."""

    for name in _LEGACY_FILES:
        try:
            portable.remove(os.path.join(index_dir, name))
        except OSError:
            pass
    for entry in os.listdir(index_dir):
        if entry.startswith("__at_") or entry.startswith("__st_"):
            try:
                portable.remove(os.path.join(index_dir, entry))
            except OSError:
                pass
    for name in _LEGACY_DIRS:
        shutil.rmtree(os.path.join(index_dir, name), True)


def _pkg_rows(pfmri, mfst_path, excludes):
    """Yield occurrences table rows (without the package id) for the
    manifest of 'pfmri' at 'mfst_path'."""

    sd = pkg.manifest.Manifest.search_dict(mfst_path, excludes)
    for (tok, at, st, fv), offsets in sd.items():
        yield (
            str(tok),
            at,
            st,
            fv,
            " ".join(str(o) for o in offsets),
        )


def _errno_wrap(e, pathname):
    """Translate permission problems into the exception the search
    machinery expects; re-raise everything else."""

    if getattr(e, "errno", None) in (errno.EACCES, errno.EROFS) or (
        isinstance(e, sqlite3.OperationalError)
        and (
            "readonly database" in str(e)
            or "unable to open database file" in str(e)
        )
    ):
        raise search_errors.ProblematicPermissionsIndexException(pathname)
    raise e


class SearchDB(object):
    """Read-side access to a client search database."""

    def __init__(self, index_dir):
        self.pathname = os.path.join(index_dir, DB_BASENAME)
        self.__con = None

    def __open(self):
        if self.__con is not None:
            return self.__con
        if not os.path.isfile(self.pathname):
            return None
        try:
            con = sqlite3.connect(
                "file:{0}?mode=ro".format(self.pathname),
                uri=True,
                isolation_level=None,
                check_same_thread=False,
            )
            uv = con.execute("PRAGMA user_version").fetchone()[0]
            if uv != SCHEMA_VERSION:
                con.close()
                return None
            complete = con.execute(
                "SELECT value FROM meta WHERE name = 'complete'"
            ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if complete is None:
            con.close()
            return None
        self.__con = con
        return con

    def close(self):
        if self.__con is not None:
            self.__con.close()
            self.__con = None

    def usable(self):
        return self.__open() is not None

    def stored_hash(self):
        """Return the recorded hash of the installed package names the
        index was built against, or None."""

        con = self.__open()
        if con is None:
            return None
        row = con.execute(
            "SELECT value FROM meta WHERE name = 'fmri-hash'"
        ).fetchone()
        if row is None:
            return None
        return row[0]

    def tokens(self):
        """Return the distinct search tokens in the index."""

        con = self.__open()
        if con is None:
            return []
        return [r[0] for r in con.execute("SELECT DISTINCT tok FROM occ")]

    def fmris(self):
        """Return (id, fmri string) pairs for the indexed packages."""

        con = self.__open()
        if con is None:
            return []
        return list(con.execute("SELECT id, fmri FROM pkgs"))

    def rows(self, toks, atype, key, pkg_ids):
        """Yield (fmri string, [offset, ...], action type, key,
        displayed value) tuples for the matching occurrences.

        'toks' restricts the results to the given list of tokens; None
        means every token. 'atype' and 'key' restrict the action type
        and key exactly when not None. 'pkg_ids' restricts the results
        to the given list of package ids; None means every package."""

        con = self.__open()
        if con is None:
            return

        conds = []
        params = []
        if atype is not None:
            conds.append("o.atype = ?")
            params.append(atype)
        if key is not None:
            conds.append("o.key = ?")
            params.append(key)
        if pkg_ids is not None:
            conds.append("o.pkg IN ({0})".format(",".join("?" * len(pkg_ids))))
            params.extend(pkg_ids)

        def q(tok_cond, tok_params):
            where = " AND ".join([tok_cond] + conds) or "1"
            return con.execute(
                "SELECT p.fmri, o.offs, o.atype, o.key, o.fval"
                " FROM occ o JOIN pkgs p ON p.id = o.pkg"
                " WHERE {0}"
                " ORDER BY o.tok, o.atype, o.key, o.fval, p.fmri".format(where),
                tok_params + params,
            )

        def gen():
            if toks is None:
                yield from q("1", [])
                return
            for i in range(0, len(toks), 500):
                chunk = list(toks)[i : i + 500]
                yield from q(
                    "o.tok IN ({0})".format(",".join("?" * len(chunk))),
                    chunk,
                )

        for fmri_str, offs, at, st, fv in gen():
            yield (
                fmri_str,
                [int(o) for o in offs.split()],
                at,
                st,
                fv,
            )


def _connect_rw(pathname):
    con = sqlite3.connect(
        pathname, isolation_level=None, check_same_thread=False
    )
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def build(
    index_dir,
    fmris,
    get_manifest_path,
    excludes=EmptyI,
    progtrack=None,
    installed_names=None,
):
    """Build a search database for the packages in 'fmris' from
    scratch, replacing any existing index (of either format) in
    'index_dir'.

    The 'installed_names' parameter is an iterable of the installed
    package name strings recorded for the staleness check; when None
    it is derived from 'fmris'."""

    if not progtrack:
        progtrack = progress.NullProgressTracker()

    fmris = list(fmris)
    if installed_names is None:
        installed_names = [f.get_fmri(anarchy=True) for f in fmris]

    tmp_path = os.path.join(index_dir, DB_BASENAME + ".tmp")
    try:
        if os.path.exists(tmp_path):
            portable.remove(tmp_path)
        con = _connect_rw(tmp_path)
    except (EnvironmentError, sqlite3.OperationalError) as e:
        _errno_wrap(e, tmp_path)

    progtrack.job_start(progtrack.JOB_REBUILD_SEARCH, goal=len(fmris))
    try:
        con.execute("BEGIN")
        for ddl in _SCHEMA:
            con.execute(ddl)
        cur = con.cursor()
        batch = []
        for pfmri in fmris:
            cur.execute(
                "INSERT INTO pkgs (fmri) VALUES (?)", (_fmri_str(pfmri),)
            )
            pkg_id = cur.lastrowid
            for row in _pkg_rows(pfmri, get_manifest_path(pfmri), excludes):
                batch.append((pkg_id,) + row)
                if len(batch) >= _INSERT_BATCH:
                    cur.executemany(
                        "INSERT INTO occ VALUES (?,?,?,?,?,?)", batch
                    )
                    batch = []
            progtrack.job_add_progress(progtrack.JOB_REBUILD_SEARCH)
        if batch:
            cur.executemany("INSERT INTO occ VALUES (?,?,?,?,?,?)", batch)
        for ddl in _INDEXES:
            con.execute(ddl)
        cur.execute(
            "INSERT INTO meta VALUES ('fmri-hash', ?)",
            (calc_hash(installed_names),),
        )
        cur.execute("INSERT INTO meta VALUES ('complete', '1')")
        con.execute("PRAGMA user_version = {0:d}".format(SCHEMA_VERSION))
        con.execute("COMMIT")
        con.close()
        _clear_legacy(index_dir)
        portable.rename(tmp_path, os.path.join(index_dir, DB_BASENAME))
    except (EnvironmentError, sqlite3.OperationalError) as e:
        con.close()
        _errno_wrap(e, tmp_path)
    finally:
        progtrack.job_done(progtrack.JOB_REBUILD_SEARCH)


def update(
    index_dir,
    plan_fmris,
    get_manifest_path,
    excludes=EmptyI,
    progtrack=None,
    installed_names=None,
):
    """Update the search database for the package changes described
    by 'plan_fmris', an iterable of (destination fmri, origin fmri)
    pairs in which either element may be None. Each changed package's
    rows are replaced individually; there is no change threshold
    beyond which the update degrades to a rebuild.

    The 'installed_names' parameter is an iterable of installed
    package name strings recorded for the staleness check."""

    if not progtrack:
        progtrack = progress.NullProgressTracker()

    plan_fmris = list(plan_fmris)
    pathname = os.path.join(index_dir, DB_BASENAME)
    try:
        con = _connect_rw(pathname)
    except (EnvironmentError, sqlite3.OperationalError) as e:
        _errno_wrap(e, pathname)

    progtrack.job_start(progtrack.JOB_UPDATE_SEARCH, goal=len(plan_fmris))
    try:
        con.execute("BEGIN")
        cur = con.cursor()

        def drop(fstr):
            row = cur.execute(
                "SELECT id FROM pkgs WHERE fmri = ?", (fstr,)
            ).fetchone()
            if row:
                cur.execute("DELETE FROM occ WHERE pkg = ?", (row[0],))
                cur.execute("DELETE FROM pkgs WHERE id = ?", (row[0],))

        for d_fmri, o_fmri in plan_fmris:
            if o_fmri:
                drop(_fmri_str(o_fmri))
            if d_fmri:
                # A repair reinstalls the same version, so any
                # existing rows must be replaced.
                drop(_fmri_str(d_fmri))
                cur.execute(
                    "INSERT INTO pkgs (fmri) VALUES (?)",
                    (_fmri_str(d_fmri),),
                )
                pkg_id = cur.lastrowid
                cur.executemany(
                    "INSERT INTO occ VALUES (?,?,?,?,?,?)",
                    (
                        (pkg_id,) + row
                        for row in _pkg_rows(
                            d_fmri, get_manifest_path(d_fmri), excludes
                        )
                    ),
                )
            progtrack.job_add_progress(progtrack.JOB_UPDATE_SEARCH)
        if installed_names is not None:
            cur.execute(
                "UPDATE meta SET value = ? WHERE name = 'fmri-hash'",
                (calc_hash(installed_names),),
            )
        con.execute("COMMIT")
    except (EnvironmentError, sqlite3.OperationalError) as e:
        _errno_wrap(e, pathname)
    finally:
        con.close()
        progtrack.job_done(progtrack.JOB_UPDATE_SEARCH)
