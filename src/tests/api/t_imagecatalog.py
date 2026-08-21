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

from . import testutils

if __name__ == "__main__":
    testutils.setup_environment("../../../proto")
import pkg5unittest

import os
import unittest

import pkg.catalog as catalog
import pkg.client.imagecatalog as imagecatalog
import pkg.fmri
from pkg.client.debugvalues import DebugValues

DEP = catalog.Catalog.DEPENDENCY
SUMM = catalog.Catalog.SUMMARY


class TestImageCatalogUnit(pkg5unittest.Pkg5TestCase):
    """Differential tests of the sqlite image state database against
    identically-populated JSON catalogs, without an image."""

    V1 = "1.0,5.11-0.2:20260101T000000Z"
    V2 = "1.0,5.11-0.10:20260201T000000Z"  # branch 0.10 > 0.2
    V3 = "2.0,5.11-0.2:20260301T000000Z"
    NOTS = "3.0,5.11-1"  # no timestamp

    # (pub, stem, ver, states, deps, summaries, de summaries)
    entries = [
        (
            "pa",
            "web/server",
            V1,
            [2, 4],
            ["depend fmri=pkg:/lib type=require"],
            ["set name=pkg.summary value=srv"],
            None,
        ),
        (
            "pb",
            "web/server",
            V1,
            [2],
            ["depend fmri=pkg:/lib type=require"],
            ["set name=pkg.summary value=srv-b"],
            ["set name=pkg.summary:de value=srv-de"],
        ),
        (
            "pa",
            "lib",
            V1,
            [2, 3],
            None,
            ["set name=pkg.summary value=lib-old"],
            None,
        ),
        (
            "pa",
            "lib",
            V2,
            [2],
            ["set name=pkg.obsolete value=true"],
            ["set name=pkg.summary value=lib-mid"],
            None,
        ),
        (
            "pa",
            "lib",
            V3,
            [2],
            None,
            ["set name=pkg.summary value=lib-new"],
            None,
        ),
        (
            "pa",
            "docs",
            NOTS,
            [2],
            None,
            ["set name=pkg.summary value=docs"],
            None,
        ),
    ]
    installed = set([("pa", "lib", V2), ("pa", "web/server", V1)])

    def setUp(self):
        pkg5unittest.Pkg5TestCase.setUp(self)

        kcat = catalog.Catalog(
            batch_mode=True, meta_root=self.test_root + "/known", sign=False
        )
        icat = catalog.Catalog(
            batch_mode=True,
            meta_root=self.test_root + "/installed",
            sign=False,
        )
        kb = kcat.get_part("catalog.base.C")
        kd = kcat.get_part("catalog.dependency.C")
        ks = kcat.get_part("catalog.summary.C")
        kde = kcat.get_part("catalog.summary.de")
        ib = icat.get_part("catalog.base.C")

        for pub, stem, ver, states, deps, summs, summs_de in self.entries:
            bentry = {
                "version": ver,
                "signature-sha-1": "sig-{0}-{1}".format(stem, ver),
                "metadata": {"states": states},
            }
            kb.add(metadata=dict(bentry), pub=pub, stem=stem, ver=ver)
            for part, actions in (
                (kd, deps),
                (ks, summs),
                (kde, summs_de),
            ):
                if actions:
                    part.add(
                        metadata={"version": ver, "actions": actions},
                        pub=pub,
                        stem=stem,
                        ver=ver,
                    )
            if (pub, stem, ver) in self.installed:
                ib.add(metadata=dict(bentry), pub=pub, stem=stem, ver=ver)

        for cat in (kcat, icat):
            os.makedirs(cat.meta_root, exist_ok=True)
            cat.finalize()
            cat.save()

        self.kcat = kcat
        self.icat = icat
        dbpath = os.path.join(self.test_root, imagecatalog.DB_BASENAME)
        imagecatalog.build_db(dbpath, kcat, icat)
        self.db = imagecatalog.ImageCatalog(dbpath)
        self.dbi = imagecatalog.ImageCatalog(dbpath, installed=True)

    def test_01_meta(self):
        self.assertTrue(self.db.usable())
        self.assertEqual(
            self.db.last_modified(),
            catalog.datetime_to_basic_ts(self.kcat.last_modified),
        )

    def test_02_tuples_and_fmris(self):
        for cat, view in ((self.kcat, self.db), (self.icat, self.dbi)):
            for last in (False, True):
                self.assertEqual(
                    list(cat.tuples(last=last, ordered=True)),
                    list(view.tuples(last=last, ordered=True)),
                )
                self.assertEqual(
                    sorted(cat.tuples(last=last)),
                    sorted(view.tuples(last=last)),
                )
        self.assertEqual(
            list(self.kcat.fmris(objects=False, ordered=True)),
            list(self.db.fmris(objects=False, ordered=True)),
        )
        self.assertEqual(
            list(self.kcat.fmris(ordered=True)),
            list(self.db.fmris(ordered=True)),
        )

    def test_03_names_counts(self):
        self.assertEqual(self.kcat.names(), self.db.names())
        self.assertEqual(list(self.kcat.pkg_names()), list(self.db.pkg_names()))
        self.assertEqual(set(self.kcat.publishers()), self.db.publishers())
        self.assertEqual(
            (self.kcat.package_count, self.kcat.package_version_count),
            self.db.package_counts(),
        )
        self.assertEqual(
            (self.icat.package_count, self.icat.package_version_count),
            self.dbi.package_counts(),
        )

    def test_04_fmris_by_version(self):
        for stem in self.kcat.names():
            j = [
                (str(v), type(v).__name__, [str(f) for f in fl])
                for v, fl in self.kcat.fmris_by_version(stem)
            ]
            d = [
                (str(v), type(v).__name__, [str(f) for f in fl])
                for v, fl in self.db.fmris_by_version(stem)
            ]
            self.assertEqual(j, d, stem)

    def test_05_entries(self):
        for info in ((DEP,), (SUMM,), (DEP, SUMM)):
            jm = {}
            for f, entry in self.kcat.entries(info_needed=info, ordered=True):
                jm[(f.publisher, f.pkg_name, str(f.version))] = entry
            dm = {}
            for t, base, actions in self.db.entry_actions(info, ordered=True):
                e = dict(base)
                if actions:
                    e["actions"] = actions
                dm[t] = e
            self.assertEqual(jm, dm, info)

    def test_06_point_lookups(self):
        for f, entry in self.kcat.entries():
            t = (f.publisher, f.pkg_name, str(f.version))
            self.assertEqual(
                self.kcat.get_entry(f), self.db.get_entry(pfmri=f), t
            )
            self.assertEqual(
                self.kcat.get_entry(f, info_needed=(DEP, SUMM)).get(
                    "actions", []
                ),
                self.db.get_entry_actions(t, (DEP, SUMM)),
                t,
            )

    def test_07_locales(self):
        t = ("pb", "web/server", self.V1)
        da = self.db.get_entry_actions(t, (SUMM,), locales=set(("C", "de")))
        self.assertTrue(any(":de" in a for a in da))
        da = self.db.get_entry_actions(t, (SUMM,))
        self.assertFalse(any(":de" in a for a in da))

    def test_08_installed_view(self):
        self.assertEqual(set(self.dbi.tuples()), set(self.icat.tuples()))
        self.assertIsNone(
            self.dbi.get_entry(pub="pa", stem="docs", ver=self.NOTS)
        )


class TestImageCatalogImage(pkg5unittest.SingleDepotTestCase):
    """Tests of the write-through image state database built by
    Image.__rebuild_image_catalogs (enabled by default; the
    no-image-state-db debug value opts out)."""

    amber10 = """
            open amber@1.0,5.11-0
            add depend fmri=pkg:/bronze@1.0 type=require
            add set name=pkg.summary value="amber pkg"
            add dir mode=0755 owner=root group=bin path=etc
            close """

    amber20 = """
            open amber@2.0,5.11-0
            add set name=pkg.summary value="newer amber"
            add set name=pkg.obsolete value=true
            close """

    bronze10 = """
            open bronze@1.0,5.11-0
            add set name=pkg.summary value="bronze pkg"
            add dir mode=0755 owner=root group=bin path=etc
            close """

    def setUp(self):
        pkg5unittest.SingleDepotTestCase.setUp(self)
        self.pkgsend_bulk(
            self.rurl, (self.amber10, self.amber20, self.bronze10)
        )
        DebugValues.pop("no-image-state-db", None)

    def tearDown(self):
        DebugValues.pop("no-image-state-db", None)
        pkg5unittest.SingleDepotTestCase.tearDown(self)

    def __db_path(self, img):
        return os.path.join(img.imgdir, "state", imagecatalog.DB_BASENAME)

    def __assert_matches(self, img):
        """Assert the state database is present, consistent with the
        image's catalog objects, and the only state store (images are
        created at version 5)."""

        self.assertEqual(img.version, 5)
        kcat = img.get_catalog(img.IMG_CATALOG_KNOWN)
        icat = img.get_catalog(img.IMG_CATALOG_INSTALLED)
        self.assertTrue(isinstance(kcat, imagecatalog.StateCatalog))
        dbpath = self.__db_path(img)
        self.assertTrue(os.path.exists(dbpath))
        statedir = os.path.dirname(dbpath)
        for legacy in ("known", "installed"):
            self.assertFalse(
                os.path.exists(os.path.join(statedir, legacy)), legacy
            )
        db = imagecatalog.ImageCatalog(dbpath)
        dbi = imagecatalog.ImageCatalog(dbpath, installed=True)
        try:
            self.assertTrue(db.usable())
            self.assertEqual(
                db.last_modified(),
                catalog.datetime_to_basic_ts(kcat.last_modified),
            )
            self.assertEqual(
                list(kcat.tuples(ordered=True)),
                list(db.tuples(ordered=True)),
            )
            self.assertEqual(
                list(icat.tuples(ordered=True)),
                list(dbi.tuples(ordered=True)),
            )
            self.assertEqual(
                (kcat.package_count, kcat.package_version_count),
                db.package_counts(),
            )
        finally:
            db.close()
            dbi.close()

    def test_01_write_through(self):
        """The database is created at image creation, tracks the known
        catalog exactly, and is kept in step across package operations
        and rebuilds without requiring a refresh."""

        api_inst = self.image_create(self.rurl)
        self.__assert_matches(api_inst.img)

        # Installed state changes are written through incrementally,
        # with no refresh needed.
        self._api_install(api_inst, ["amber@1.0"])
        img = api_inst.img
        self.__assert_matches(img)

        # Installed state must be visible in the installed view.
        dbi = imagecatalog.ImageCatalog(self.__db_path(img), installed=True)
        try:
            stems = set(stem for pub, stem, ver in dbi.tuples())
            self.assertEqual(stems, set(["amber", "bronze"]))
        finally:
            dbi.close()

        self._api_uninstall(api_inst, ["amber", "bronze"])
        self.__assert_matches(api_inst.img)

        # A full refresh rebuilds the database wholesale; it must
        # still match afterwards.
        api_inst.refresh(immediate=True, full_refresh=True)
        self.__assert_matches(self.get_img_api_obj().img)

    def test_02_solver(self):
        """With the flag set and a current database, the solver is
        handed a database-backed catalog and resolves dependencies
        through it."""

        api_inst = self.image_create(self.rurl)
        self.assertTrue(
            isinstance(
                api_inst.img.get_solver_catalog(),
                imagecatalog.SolverCatalog,
            )
        )

        # amber@1.0 depends on bronze; installing it exercises
        # dependency resolution through the database-backed catalog.
        self._api_install(api_inst, ["amber@1.0"])
        img = api_inst.img
        icat = img.get_catalog(img.IMG_CATALOG_INSTALLED)
        self.assertEqual(
            set(stem for pub, stem, ver in icat.tuples()),
            set(["amber", "bronze"]),
        )
        self.__assert_matches(img)

        # On version 5 images the database is the only store, so the
        # opt-out debug value has no effect on catalog selection.
        DebugValues["no-image-state-db"] = 1
        try:
            self.assertTrue(
                isinstance(
                    api_inst.img.get_solver_catalog(),
                    imagecatalog.SolverCatalog,
                )
            )
        finally:
            DebugValues.pop("no-image-state-db", None)

    def test_03_list(self):
        """Package listing through the database-backed catalogs
        produces results identical to the JSON implementation."""

        import pkg.client.api as api

        api_inst = self.image_create(self.rurl)
        self._api_install(api_inst, ["amber@1.0"])

        self.assertTrue(
            isinstance(
                api_inst.img.get_list_catalog(api_inst.img.IMG_CATALOG_KNOWN),
                imagecatalog.ListCatalog,
            )
        )

        def snap(ltype, **kw):
            return list(
                api_inst.get_pkg_list(ltype, raise_unmatched=False, **kw)
            )

        for ltype in (
            api.ImageInterface.LIST_ALL,
            api.ImageInterface.LIST_INSTALLED,
            api.ImageInterface.LIST_INSTALLED_NEWEST,
            api.ImageInterface.LIST_NEWEST,
        ):
            for kw in (
                {},
                {"patterns": ["amber"]},
                {"patterns": ["amb*"]},
                {"patterns": ["amber@1.0", "bronze"]},
                {"patterns": ["nosuchpkg"]},
                {"variants": True},
            ):
                dbl = snap(ltype, **kw)
                DebugValues["no-image-state-db"] = 1
                try:
                    jsl = snap(ltype, **kw)
                finally:
                    DebugValues.pop("no-image-state-db", None)
                self.assertEqual(jsl, dbl, (ltype, kw))


if __name__ == "__main__":
    unittest.main()
