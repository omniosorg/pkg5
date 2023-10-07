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
#
# }}}

# Copyright 2022 OmniOS Community Edition (OmniOSce) Association.

# The default 'Aligned' wrapper only preserves field alignment if the field
# starts off aligned. This is a subclass that always leaves the file offset
# properly aligned, ready for the next field.
#
# See https://github.com/construct/construct/issues/980

from construct import (
    Aligned as _Aligned,
    stream_tell,
    stream_read,
    stream_write,
)


class Aligned(_Aligned):
    def _pad(self, stream, path):
        v = self.modulus - 1
        pos = stream_tell(stream, path)
        newpos = (pos + v) & ~v
        return newpos - pos

    def _parse(self, stream, context, path):
        obj = self.subcon._parsereport(stream, context, path)
        pad = self._pad(stream, path)
        stream_read(stream, pad, path)
        return obj

    def _build(self, obj, stream, context, path):
        buildret = self.subcon._build(obj, stream, context, path)
        pad = self._pad(stream, path)
        stream_write(stream, self.pattern * pad, pad, path)
        return buildret
