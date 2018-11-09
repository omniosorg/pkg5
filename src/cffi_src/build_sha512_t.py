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

#
# Copyright (c) 2015, Oracle and/or its affiliates. All rights reserved.
# Copyright 2018 OmniOS Community Edition (OmniOSce) Association.
#

from __future__ import unicode_literals
from cffi import FFI

ffi = FFI()

ffi.set_source("_sha512_t", """
/* Includes */
#include <sys/sha2.h>
#include <string.h>
""")

ffi.cdef("""
#define	SHA512_224 9
#define	SHA512_256 10

/* Types */
typedef struct _sha2_ctx {
        uint32_t algotype;
        union {
                uint32_t s32[8];
                uint64_t s64[8];
        } state;
        union {
                uint32_t c32[2];
                uint64_t c64[2];
        } count;
        union {
                uint8_t         buf8[128];
                uint32_t        buf32[32];
                uint64_t        buf64[16];
        } buf_un;
} SHA2_CTX;

/* Functions */
void SHA2Init(uint64_t t_bits, SHA2_CTX *ctx);
void SHA2Update(SHA2_CTX *ctx, const void *buf, size_t bufsz);
void SHA2Final(void *digest, SHA2_CTX *ctx);
void *memcpy(void *restrict s1, const void *restrict s2, size_t n);
""")

if __name__ == "__main__":
    ffi.compile(tmpdir="./cffi_src")
