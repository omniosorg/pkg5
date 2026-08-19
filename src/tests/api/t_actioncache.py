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
import sqlite3
import unittest

import pkg.client.actioncache as actioncache
from pkg.client.debugvalues import DebugValues
from pkg.client.imageplan import ImagePlan


class TestActionCache(pkg5unittest.SingleDepotTestCase):
    """Tests for the sqlite installed-action cache that replaced the
    actions.stripped/actions.offsets/keys.conflicting flat files."""

    persistent_setup = False

    amber10 = """
            open amber@1.0,5.11-0
            add dir mode=0755 owner=root group=bin path=etc
            add file amber1 mode=0644 owner=root group=bin path=etc/amber1
            add link path=etc/amber-link target=amber1
            close """

    bronze10 = """
            open bronze@1.0,5.11-0
            add dir mode=0755 owner=root group=bin path=etc
            add file bronze1 mode=0644 owner=root group=bin path=etc/bronze1
            close """

    bronze20 = """
            open bronze@2.0,5.11-0
            add dir mode=0755 owner=root group=bin path=etc
            add file bronze1 mode=0644 owner=root group=bin path=etc/bronze1
            add file bronze2 mode=0644 owner=root group=bin path=etc/bronze2
            close """

    # Delivers the same path as amber's etc/amber1 with different
    # content and mode; installing both is a conflict.
    clash10 = """
            open clash@1.0,5.11-0
            add dir mode=0755 owner=root group=bin path=etc
            add file clash1 mode=0600 owner=root group=bin path=etc/amber1
            close """

    varpkg10 = """
            open varpkg@1.0,5.11-0
            add dir mode=0755 owner=root group=bin path=var
            add file vp1 mode=0644 owner=root group=bin path=var/vp \
                variant.opensolaris.zone=global
            add file vp2 mode=0644 owner=root group=bin path=var/vp \
                variant.opensolaris.zone=nonglobal
            close """

    misc_files = ["amber1", "bronze1", "bronze2", "clash1", "vp1", "vp2"]

    def setUp(self):
        pkg5unittest.SingleDepotTestCase.setUp(self)
        self.make_misc_files(self.misc_files)
        self.pkgsend_bulk(
            self.rurl,
            (
                self.amber10,
                self.bronze10,
                self.bronze20,
                self.clash10,
                self.varpkg10,
            ),
        )

    @staticmethod
    def __legacy_bad_keys(img):
        """Compute conflicting keys the way the flat-file code used
        to: build the full namespace dictionary from every installed
        manifest and run ImagePlan._check_actions over it."""

        nsd = {}
        excludes = img.list_excludes()
        for pfmri in img.gen_installed_pkgs():
            m = img.get_manifest(pfmri, ignore_excludes=True)
            for act in m.gen_actions(excludes=excludes):
                if not act.globally_identical:
                    continue
                act.strip()
                nsd.setdefault(act.namespace_group, {})
                nsd[act.namespace_group].setdefault(act.attrs[act.key_attr], [])
                nsd[act.namespace_group][act.attrs[act.key_attr]].append(
                    (act, pfmri)
                )
        return ImagePlan._check_actions(nsd)

    @staticmethod
    def __dump(cache):
        """Return the comparable content of a cache database as
        (package fmris, action rows, conflict rows)."""

        con = sqlite3.connect(cache.pathname)
        try:
            pkgs = frozenset(
                r[0] for r in con.execute("SELECT fmri FROM packages")
            )
            rows = frozenset(
                con.execute(
                    "SELECT p.fmri, a.aname, a.keyval, a.act"
                    " FROM actions a"
                    " JOIN packages p ON p.pkg_id = a.pkg_id"
                )
            )
            conf = frozenset(con.execute("SELECT ns, keyval FROM conflicts"))
        finally:
            con.close()
        return pkgs, rows, conf

    def __assert_cache_matches(self, img):
        """Assert that the image's (incrementally maintained) action
        cache is identical to one rebuilt from scratch, and that its
        conflicts match the legacy computation."""

        cache = img.get_action_cache()
        self.assertTrue(cache.is_fresh())

        ref = actioncache.ActionCache(img, img.temporary_dir())
        ref.rebuild()
        try:
            self.assertEqual(self.__dump(cache), self.__dump(ref))
        finally:
            ref.close()

        self.assertEqual(cache.conflicting_keys(), self.__legacy_bad_keys(img))

    # Note that api_obj.reset(), which runs after every executed
    # operation, recreates the underlying Image object, so tests must
    # re-fetch api_inst.img after each operation rather than holding
    # on to a stale Image reference.

    def test_01_lifecycle(self):
        """The cache tracks install, update and uninstall operations
        and always matches a from-scratch rebuild."""

        api_inst = self.image_create(self.rurl)

        self._api_install(api_inst, ["amber", "bronze@1.0"])
        img = api_inst.img
        cache = img.get_action_cache()
        self.assertTrue(
            os.path.exists(cache.pathname), "actions.sqlite created"
        )
        self.assertFalse(cache.has_conflicts())
        self.__assert_cache_matches(img)

        # The legacy flat files must not be left around where an
        # older client could trust them.
        cdir = os.path.dirname(cache.pathname)
        for fname in (
            "actions.stripped",
            "actions.offsets",
            "keys.conflicting",
        ):
            self.assertFalse(os.path.exists(os.path.join(cdir, fname)))

        self._api_update(api_inst, pkgs_update=["bronze@2.0"])
        self.__assert_cache_matches(api_inst.img)

        self._api_uninstall(api_inst, ["amber"])
        self.__assert_cache_matches(api_inst.img)

    def test_02_stale_cache_reconciled(self):
        """A cache that is out of step with the installed catalog
        (for instance after a boot environment rollback) is repaired
        incrementally on next use."""

        api_inst = self.image_create(self.rurl)
        self._api_install(api_inst, ["amber"])

        cache = api_inst.img.get_action_cache()
        path = cache.pathname

        # Install another package, then put the pre-install database
        # back, simulating out-of-band modification.
        saved = open(path, "rb").read()
        self._api_install(api_inst, ["bronze@1.0"])
        cache.close()
        with open(path, "wb") as fh:
            fh.write(saved)

        img2 = self.get_img_api_obj().img
        self.__assert_cache_matches(img2)

    def test_03_conflicts(self):
        """Conflicting actions are recorded in the conflicts table
        and maintained incrementally as packages come and go."""

        DebugValues["broken-conflicting-action-handling"] = 1
        try:
            api_inst = self.image_create(self.rurl)

            self._api_install(api_inst, ["amber", "clash"])
            img = api_inst.img
            cache = img.get_action_cache()
            self.assertTrue(cache.has_conflicts())
            self.assertTrue("etc/amber1" in cache.conflicting_keys())
            self.__assert_cache_matches(img)

            self._api_uninstall(api_inst, ["clash"])
            img = api_inst.img
            cache = img.get_action_cache()
            self.assertFalse(cache.has_conflicts())
            self.__assert_cache_matches(img)
        finally:
            DebugValues.pop("broken-conflicting-action-handling", None)

    def test_04_variant_change(self):
        """Changing variants changes the excludes signature and
        forces a full rebuild with the new excludes."""

        variants = {"variant.opensolaris.zone": "global"}
        api_inst = self.image_create(self.rurl, variants=variants)

        self._api_install(api_inst, ["varpkg"])
        img = api_inst.img
        cache = img.get_action_cache()
        rows = self.__dump(cache)[1]
        acts = [r[3] for r in rows if r[2] == "var/vp"]
        self.assertEqual(len(acts), 1)
        global_act = acts[0]
        self.__assert_cache_matches(img)

        self._api_change_varcets(
            api_inst,
            variants={"variant.opensolaris.zone": "nonglobal"},
        )
        img = api_inst.img
        cache = img.get_action_cache()
        rows = self.__dump(cache)[1]
        acts = [r[3] for r in rows if r[2] == "var/vp"]
        self.assertEqual(len(acts), 1)
        # The cache must now hold the nonglobal variant of the file.
        self.assertNotEqual(acts[0], global_act)
        self.__assert_cache_matches(img)

    def test_05_missing_and_corrupt(self):
        """A missing or corrupt database is rebuilt transparently."""

        api_inst = self.image_create(self.rurl)
        self._api_install(api_inst, ["amber"])

        cache = api_inst.img.get_action_cache()
        path = cache.pathname
        cache.close()

        os.unlink(path)
        img2 = self.get_img_api_obj().img
        self.__assert_cache_matches(img2)
        self.assertTrue(os.path.exists(path))

        with open(path, "r+b") as fh:
            fh.seek(100)
            fh.write(b"garbage" * 300)
        img3 = self.get_img_api_obj().img
        self.__assert_cache_matches(img3)

    def test_06_verify_debug(self):
        """With -D actioncache-verify=1 set, a database that has
        drifted from the installed manifests (here: tampered rows for
        a package the operation doesn't touch, which an ordinary
        reconcile would preserve) is detected after the operation and
        rebuilt."""

        api_inst = self.image_create(self.rurl)
        self._api_install(api_inst, ["amber"])

        cache = api_inst.img.get_action_cache()
        self.assertIsNone(cache.selfcheck())
        path = cache.pathname
        cache.close()

        con = sqlite3.connect(path)
        con.execute("DELETE FROM actions WHERE aname = 'link'")
        con.commit()
        con.close()

        DebugValues["actioncache-verify"] = 1
        try:
            self._api_install(api_inst, ["bronze@1.0"])
        finally:
            DebugValues.pop("actioncache-verify", None)

        cache = api_inst.img.get_action_cache()
        self.assertIsNone(cache.selfcheck())
        self.__assert_cache_matches(api_inst.img)


if __name__ == "__main__":
    unittest.main()
