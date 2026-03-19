#!/usr/bin/python3
#
# {{{ CDDL HEADER
#
# This file and its contents are supplied under the terms of the
# Common Development and Distribution License ("CDDL"), version 1.0.
# You may only use this file in accordance with the terms of version
# 1.0 of the CDDL.
#
# A full copy of the text of the CDDL should have accompanied this
# source. A copy of the CDDL is also available via the Internet at
# http://www.illumos.org/license/CDDL.
# }}}

# Copyright 2020 OmniOS Community Edition (OmniOSce) Association.

"""
json module abstraction for the packaging system.
"""

from pkg.client.debugvalues import DebugValues
import pkg.misc as misc

# Pass-through to jsonschema
from jsonschema import validate, ValidationError

import os, sys, time
import rapidjson as _json

JSONDecodeError = _json.JSONDecodeError


def _start():
    psinfo = misc.ProcFS.psinfo()
    _start.rss = psinfo.pr_rssize
    _start.start = time.time()
    if int(DebugValues["json"]) > 1:
        _start.trace = True


_start.rss = 0
_start.maxrss = 0
_start.start = 0
_start.trace = False


def _end(func, param, ret):
    taken = time.time() - _start.start
    psinfo = misc.ProcFS.psinfo()
    mem = (psinfo.pr_rssize - _start.rss) / 1024.0
    if psinfo.pr_rssize > _start.maxrss:
        _start.maxrss = psinfo.pr_rssize
    print(
        "JSON Mem {:.2f}+{:.2f}={:.2f} Max {:.2f} MiB "
        "- Time {:.2f}s - {}({}) = {}".format(
            _start.rss / 1024.0,
            mem,
            psinfo.pr_rssize / 1024.0,
            _start.maxrss / 1024.0,
            taken,
            func,
            param,
            ret,
        ),
        file=sys.stderr,
    )


def _stack(note, limit=5):
    from traceback import extract_stack, format_list

    stack = extract_stack(limit=limit)[:-3]
    print("{}:".format(note), file=sys.stderr)
    for l in format_list(stack):
        for m in l.split("\n"):
            if m.strip() == "":
                continue
            print("    >> {}".format(m), file=sys.stderr)


def _file(stream):
    try:
        name = stream.name
        size = os.path.getsize(name) / (1024.0 * 1024.0)
        return "{:.2f} MiB {}".format(size, name)
    except:
        return str(stream)


def load(stream, **kw):
    if "json" in DebugValues:
        _start()
        ret = _json.load(stream, **kw)
        _end("load", _file(stream), "")
        return ret
    else:
        return _json.load(stream, **kw)


def loads(str, **kw):
    if "json" in DebugValues:
        _start()
        ret = _json.loads(str, **kw)
        _end("loads", len(str), "")
        return ret
    else:
        return _json.loads(str, **kw)


def dump(obj, stream, **kw):
    if "json" in DebugValues:
        _start()
        ret = _json.dump(obj, stream, **kw)
        _end("dump", _file(stream), ret)
        return ret
    else:
        return _json.dump(obj, stream, **kw)


def dumps(obj, **kw):
    if "json" in DebugValues:
        _start()
        ret = _json.dumps(obj, **kw)
        _end("dumps", "", len(ret))
        return ret
    else:
        return _json.dumps(obj, **kw)


# Vim hints
# vim:ts=4:sw=4:et:fdm=marker
