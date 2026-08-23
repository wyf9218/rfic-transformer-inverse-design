#!/usr/bin/env python3
"""Exact, result-free runtime identity smoke (v10).

The authenticated smoke body is intentionally a verifier, not a builder or
pipeline launcher.  This module also exports one narrowly scoped preflight
helper that starts and reaps exactly one held-FD smoke child with bounded
capture; timeout/overflow termination applies only to that owned child.  The
smoke body may only import NumPy and Matplotlib from a previously published,
frozen private runtime.  It has no network, controller, external watcher,
result, deployment, or unrelated process-control path.  Its only watcher is an in-process Linux
kernel inotify descriptor used to monitor the frozen ROOT's directory entries
and held regular inodes for the explicitly configured failure masks; setup and
terminal digest/inventory comparisons independently close the monitored
interval.  It starts no external watcher or process.

The smoke authorization binds the complete ``/proc/self/cmdline`` vector.  One
value is necessarily represented by ``__SMOKE_AUTHORIZATION_SHA256__`` in the
authorized template: an authorization document cannot contain its own final
SHA-256 without an impossible self-reference.  At runtime that one actual value
must equal both the out-of-band CLI value and the SHA-256 recomputed from the
single nofollow-opened authorization byte stream.  Every other argv byte is
compared exactly.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import marshal
import os
import re
import selectors
import stat
import struct
import sys
import time
import types
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

AUTHORIZATION_SCHEMA = (
    "historical_200k_fixed10k_result_free_runtime_smoke_authorization_v10"
)
AUTHORIZATION_STATUS = "AUTHORIZED_EXACT_RESULT_FREE_RUNTIME_LAYOUT_SMOKE_V10"
BUILD_RECEIPT_SCHEMA = (
    "historical_200k_fixed10k_result_free_transport_build_pass_receipt_v10"
)
BUILD_RECEIPT_STATUS = (
    "PASS_V10_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_BUILT_NOT_SMOKED"
)
RUNTIME_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_post_stage06_runtime_dependency_identity_manifest_v10"
)
RUNTIME_MANIFEST_STATUS = "FROZEN_RESULT_FREE_RUNTIME_IDENTITY_V10"
CAPABILITY = "I_HAVE_EXACT_V10_RESULT_FREE_RUNTIME_SMOKE_AUTHORIZATION"
AUTH_SHA_PLACEHOLDER = "__SMOKE_AUTHORIZATION_SHA256__"
HELD_BUILDER_LAUNCH_SCHEMA = (
    "historical_200k_fixed10k_trusted_held_builder_launch_v1"
)
HELD_BUILDER_LAUNCH_STATUS = "AUTHORIZED_HELD_BYTES_IN_PREFLIGHT_PROCESS"
HELD_BUILDER_LAUNCH_METHOD = (
    "HELD_INTERPRETER_FD197_AND_PREAD_SHA_COMPILE_BUILDER_FD198_"
    "IN_TRUSTED_PREFLIGHT_PROCESS_V1"
)
INTERPRETER_FD = 197
SMOKE_SOURCE_FD = 198
HELD_SMOKE_BOOTSTRAP_PROTOCOL = (
    "HISTORICAL_200K_FIXED10K_RESULT_FREE_RUNTIME_SMOKE_HELD_BYTES_V10"
)
HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS = (
    "--held-smoke-source-fd",
    "--expected-held-smoke-device",
    "--expected-held-smoke-inode",
    "--expected-held-smoke-size",
    "--expected-held-smoke-mtime-ns",
    "--expected-held-smoke-ctime-ns",
    "--expected-held-smoke-mode",
    "--expected-held-smoke-nlink",
    "--expected-held-smoke-sha256",
    "--original-smoke-evidence-path",
    "--expected-bootstrap-sha256",
)
HELD_SMOKE_BOOTSTRAP_TEXT = r'''import fcntl,hashlib,json,os,stat,sys
IFD=197
SFD=198
PROTO="HISTORICAL_200K_FIXED10K_RESULT_FREE_RUNTIME_SMOKE_HELD_BYTES_V10"
PLACEHOLDER="__SMOKE_AUTHORIZATION_SHA256__"
FLAGS=("--held-smoke-source-fd","--expected-held-smoke-device","--expected-held-smoke-inode","--expected-held-smoke-size","--expected-held-smoke-mtime-ns","--expected-held-smoke-ctime-ns","--expected-held-smoke-mode","--expected-held-smoke-nlink","--expected-held-smoke-sha256","--original-smoke-evidence-path","--expected-bootstrap-sha256")
def fail(message):
 sys.stderr.write("FAIL_CLOSED_HELD_SMOKE_BOOTSTRAP: "+message+"\n")
 raise SystemExit(2)
def uint(text,label):
 if type(text) is not str or not text or (text!="0" and (text[0]=="0" or not text.isascii())) or not text.isdecimal(): fail(label+" is not canonical unsigned decimal")
 return int(text)
def sha(text,label):
 if type(text) is not str or len(text)!=64 or any(c not in "0123456789abcdef" for c in text): fail(label+" is not lowercase SHA-256")
 return text
def ident(value):
 return (value.st_mode,value.st_nlink,value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
def pread_exact(fd,size,label,limit=16777216):
 if size<0 or size>limit: fail(label+" size is outside limit")
 chunks=[]
 offset=0
 while offset<size:
  block=os.pread(fd,min(1048576,size-offset),offset)
  if not block: fail(label+" ended before authorized size")
  chunks.append(block)
  offset+=len(block)
 if os.pread(fd,1,size)!=b"": fail(label+" exceeds authorized size")
 return b"".join(chunks)
def pairs(items):
 value={}
 for key,item in items:
  if type(key) is not str or key in value: fail("authorization has duplicate/non-string key")
  value[key]=item
 return value
def reject_constant(value):
 fail("authorization contains non-finite number "+value)
def exact_object(value,keys,label):
 if type(value) is not dict or set(value)!=set(keys): fail(label+" exact object mismatch")
 return value
def exact_int(value,label):
 if type(value) is not int or value<0: fail(label+" is not nonnegative exact integer")
 return value
def exact_string(value,label):
 if type(value) is not str or not value or "\x00" in value: fail(label+" is not exact nonempty string")
 return value
afd=-1
try:
 argv=sys.argv[1:]
 envelope_count=2*len(FLAGS)
 if len(argv)<=envelope_count: fail("bootstrap envelope or smoke CLI is absent")
 if tuple(argv[:envelope_count:2])!=FLAGS: fail("bootstrap envelope flag order/form mismatch")
 values=argv[1:envelope_count:2]
 if values[0]!=str(SFD): fail("smoke source FD is not exact 198")
 device=uint(values[1],"smoke device")
 inode=uint(values[2],"smoke inode")
 size=uint(values[3],"smoke size")
 mtime_ns=uint(values[4],"smoke mtime_ns")
 ctime_ns=uint(values[5],"smoke ctime_ns")
 if values[6]!="0444": fail("smoke mode is not exact 0444")
 if values[7]!="1": fail("smoke nlink is not exact 1")
 smoke_sha=sha(values[8],"smoke SHA")
 original_path=exact_string(values[9],"original smoke evidence path")
 if not original_path.startswith("/") or os.path.normpath(original_path)!=original_path: fail("original smoke evidence path is not canonical absolute")
 bootstrap_sha=sha(values[10],"bootstrap SHA")
 smoke_argv=argv[envelope_count:]
 if len(smoke_argv)<4 or smoke_argv[0]!="--smoke-authorization" or smoke_argv[2]!="--trusted-smoke-authorization-sha256": fail("smoke authorization CLI prefix mismatch")
 auth_path=exact_string(smoke_argv[1],"smoke authorization path")
 auth_sha=sha(smoke_argv[3],"trusted smoke authorization SHA")
 if not auth_path.startswith("/") or os.path.normpath(auth_path)!=auth_path: fail("smoke authorization path is not canonical absolute")
 proc_fd=os.open("/proc/self/cmdline",os.O_RDONLY|os.O_CLOEXEC)
 try:
  proc_raw=b""
  while True:
   block=os.read(proc_fd,65536)
   if not block: break
   proc_raw+=block
 finally:
  os.close(proc_fd)
 if not proc_raw.endswith(b"\x00"): fail("/proc/self/cmdline lacks terminal NUL")
 try: proc=[item.decode("utf-8","strict") for item in proc_raw[:-1].split(b"\x00")]
 except UnicodeDecodeError: fail("/proc/self/cmdline is not strict UTF-8")
 if not proc or any(not item for item in proc): fail("/proc/self/cmdline contains empty argv member")
 if proc[:5]!=["/proc/self/fd/197","-I","-B","-S","-c"] or len(proc)<6: fail("interpreter path/flags/bootstrap mode mismatch")
 if proc[6:]!=argv: fail("Python argv differs from /proc/self/cmdline")
 if hashlib.sha256(proc[5].encode("utf-8")).hexdigest()!=bootstrap_sha: fail("executed bootstrap text SHA mismatch")
 for fd,label in ((IFD,"interpreter"),(SFD,"smoke source")):
  if fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY: fail(label+" FD is not O_RDONLY")
  if fcntl.fcntl(fd,fcntl.F_GETFD)&fcntl.FD_CLOEXEC: fail(label+" FD is CLOEXEC/not inherited")
 interpreter_info=os.fstat(IFD)
 if not stat.S_ISREG(interpreter_info.st_mode): fail("held interpreter FD is not regular")
 source_before=os.fstat(SFD)
 if not stat.S_ISREG(source_before.st_mode): fail("held smoke source FD is not regular")
 expected_ident=(source_before.st_mode,1,device,inode,size,mtime_ns,ctime_ns)
 if ident(source_before)!=expected_ident or stat.S_IMODE(source_before.st_mode)!=0o444: fail("held smoke source initial identity mismatch")
 source_bytes=pread_exact(SFD,size,"held smoke source")
 source_after=os.fstat(SFD)
 if ident(source_after)!=ident(source_before): fail("held smoke source identity changed during pread")
 if hashlib.sha256(source_bytes).hexdigest()!=smoke_sha: fail("held smoke source SHA mismatch")
 afd=os.open(auth_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 auth_before=os.fstat(afd)
 if not stat.S_ISREG(auth_before.st_mode) or auth_before.st_nlink!=1 or stat.S_IMODE(auth_before.st_mode)!=0o444: fail("smoke authorization is not 0444 single-link regular")
 auth_bytes=pread_exact(afd,auth_before.st_size,"smoke authorization")
 if ident(os.fstat(afd))!=ident(auth_before): fail("smoke authorization identity changed during pread")
 if hashlib.sha256(auth_bytes).hexdigest()!=auth_sha: fail("smoke authorization SHA mismatch")
 try: auth=json.loads(auth_bytes.decode("utf-8","strict"),object_pairs_hook=pairs,parse_constant=reject_constant)
 except (UnicodeDecodeError,json.JSONDecodeError) as exc: fail("smoke authorization strict JSON failure: "+type(exc).__name__)
 top_keys=("schema","status","decision_id","scope","authority","paths","identities","expected","bound_v8","held_byte_bootstrap","exact_process_argv_template","exact_isolation_flags","imports_exact","environment_policy","capability")
 exact_object(auth,top_keys,"authorization")
 if auth["schema"]!="historical_200k_fixed10k_result_free_runtime_smoke_authorization_v10" or auth["status"]!="AUTHORIZED_EXACT_RESULT_FREE_RUNTIME_LAYOUT_SMOKE_V10": fail("authorization schema/status mismatch")
 held=exact_object(auth["held_byte_bootstrap"],("contract","smoke_source_identity","smoke_source_sha256","original_smoke_evidence_path"),"authorization held bootstrap")
 contract=exact_object(held["contract"],("protocol","interpreter_fd","smoke_source_fd","interpreter_proc_path","smoke_source_proc_path","isolation_flags","launch_mode","envelope_flags","bootstrap_sha256"),"authorization held bootstrap contract")
 if contract["protocol"]!=PROTO or exact_int(contract["interpreter_fd"],"contract interpreter_fd")!=IFD or exact_int(contract["smoke_source_fd"],"contract smoke_source_fd")!=SFD: fail("authorization held FD contract mismatch")
 if contract["interpreter_proc_path"]!="/proc/self/fd/197" or contract["smoke_source_proc_path"]!="/proc/self/fd/198" or contract["isolation_flags"]!=["-I","-B","-S"] or contract["launch_mode"]!="CPYTHON_DASH_C_PREAD_HELD_SOURCE_COMPILE_EXEC_V1" or contract["envelope_flags"]!=list(FLAGS) or contract["bootstrap_sha256"]!=bootstrap_sha: fail("authorization static bootstrap contract mismatch")
 source_identity=exact_object(held["smoke_source_identity"],("device","inode","size_bytes","mtime_ns","ctime_ns","mode","nlink"),"authorization smoke source identity")
 expected_source={"device":device,"inode":inode,"size_bytes":size,"mtime_ns":mtime_ns,"ctime_ns":ctime_ns,"mode":"0444","nlink":1}
 if any(type(source_identity[key]) is not type(expected_source[key]) for key in expected_source) or source_identity!=expected_source: fail("authorization/envelope smoke source identity mismatch")
 if held["smoke_source_sha256"]!=smoke_sha or held["original_smoke_evidence_path"]!=original_path: fail("authorization/envelope smoke source provenance mismatch")
 expected=exact_object(auth["expected"],("build_pass_receipt_sha256","build_authorization_sha256","build_commit_intent_sha256","runtime_manifest_sha256","files_only_runtime_root_digest","files_only_private_root_digest","structural_private_tree_digest","files_only_full_root_digest","structural_full_root_digest","empty_scratch_inventory_digest","source_python_sha256","smoke_script_sha256"),"authorization expected")
 paths=exact_object(auth["paths"],("smoke_authorization","build_pass_receipt","final_root","scratch_dir","source_python","smoke_script"),"authorization paths")
 if expected["smoke_script_sha256"]!=smoke_sha or paths["smoke_script"]!=original_path or paths["smoke_authorization"]!=auth_path: fail("authorization source/auth evidence binding mismatch")
 interpreter_sha=sha(expected["source_python_sha256"],"authorized source Python SHA")
 interpreter_before=os.fstat(IFD)
 if not stat.S_ISREG(interpreter_before.st_mode) or interpreter_before.st_nlink!=1: fail("held interpreter is not single-link regular")
 interpreter_bytes=pread_exact(IFD,interpreter_before.st_size,"held interpreter",268435456)
 if ident(os.fstat(IFD))!=ident(interpreter_before): fail("held interpreter identity changed during pread")
 if hashlib.sha256(interpreter_bytes).hexdigest()!=interpreter_sha: fail("held interpreter SHA differs from authorization")
 executable_fd=os.open("/proc/self/exe",os.O_RDONLY|os.O_CLOEXEC)
 try:
  executable_before=os.fstat(executable_fd)
  executable_bytes=pread_exact(executable_fd,executable_before.st_size,"/proc/self/exe",268435456)
  if ident(os.fstat(executable_fd))!=ident(executable_before): fail("/proc/self/exe identity changed during pread")
 finally:
  os.close(executable_fd)
 if (executable_before.st_dev,executable_before.st_ino)!=(interpreter_before.st_dev,interpreter_before.st_ino): fail("held FD197 does not identify /proc/self/exe")
 if hashlib.sha256(executable_bytes).hexdigest()!=interpreter_sha: fail("/proc/self/exe SHA differs from held FD197/authorization")
 template=auth["exact_process_argv_template"]
 if type(template) is not list or not all(type(item) is str and item for item in template) or template.count(PLACEHOLDER)!=1: fail("authorization argv template shape mismatch")
 placeholder_index=template.index(PLACEHOLDER)
 if placeholder_index==0 or template[placeholder_index-1]!="--trusted-smoke-authorization-sha256": fail("authorization SHA placeholder slot mismatch")
 normalized=list(proc)
 if placeholder_index>=len(normalized) or normalized[placeholder_index]!=auth_sha: fail("actual authorization SHA slot mismatch")
 normalized[placeholder_index]=PLACEHOLDER
 if normalized!=template: fail("exact /proc/self/cmdline authorization mismatch")
 context={"protocol":PROTO,"interpreter_fd":IFD,"smoke_source_fd":SFD,"interpreter_identity":{"device":interpreter_before.st_dev,"inode":interpreter_before.st_ino,"size_bytes":interpreter_before.st_size,"mtime_ns":interpreter_before.st_mtime_ns,"ctime_ns":interpreter_before.st_ctime_ns,"mode":format(stat.S_IMODE(interpreter_before.st_mode),"04o"),"nlink":interpreter_before.st_nlink},"interpreter_sha256":interpreter_sha,"smoke_source_identity":expected_source,"smoke_source_sha256":smoke_sha,"original_smoke_evidence_path":original_path,"bootstrap_sha256":bootstrap_sha,"actual_cmdline":proc,"authorization_fd":afd,"authorization_identity":{"device":auth_before.st_dev,"inode":auth_before.st_ino,"size_bytes":auth_before.st_size,"mtime_ns":auth_before.st_mtime_ns,"ctime_ns":auth_before.st_ctime_ns,"mode":"0444","nlink":1},"authorization_sha256":auth_sha}
 namespace={"__name__":"_result_free_runtime_smoke_v10_held_bytes__","__file__":"/proc/self/fd/198","__package__":None}
 exec(compile(source_bytes,"/proc/self/fd/198","exec",dont_inherit=True,optimize=0),namespace,namespace)
 entry=namespace.get("held_byte_bootstrap_main")
 if not callable(entry): fail("held smoke source lacks authenticated entry")
 result=entry(tuple(smoke_argv),context)
 if type(result) is not int or result!=0: fail("held smoke entry returned nonzero/non-integer")
 raise SystemExit(0)
except SystemExit:
 raise
except BaseException as exc:
 fail(type(exc).__name__+": "+str(exc))
finally:
 if afd>=0:
  os.close(afd)
'''
HELD_SMOKE_BOOTSTRAP_SHA256 = (
    "a38e950b705e12cb07c30148a7e2fedf5b60e6c17c8d49a21984973cda1a34b4"
)
if hashlib.sha256(HELD_SMOKE_BOOTSTRAP_TEXT.encode("utf-8")).hexdigest() != (
    HELD_SMOKE_BOOTSTRAP_SHA256
):
    raise RuntimeError("frozen held smoke bootstrap text/SHA mismatch")
HELD_SMOKE_BOOTSTRAP_CONTRACT = {
    "protocol": HELD_SMOKE_BOOTSTRAP_PROTOCOL,
    "interpreter_fd": INTERPRETER_FD,
    "smoke_source_fd": SMOKE_SOURCE_FD,
    "interpreter_proc_path": f"/proc/self/fd/{INTERPRETER_FD}",
    "smoke_source_proc_path": f"/proc/self/fd/{SMOKE_SOURCE_FD}",
    "isolation_flags": ["-I", "-B", "-S"],
    "launch_mode": "CPYTHON_DASH_C_PREAD_HELD_SOURCE_COMPILE_EXEC_V1",
    "envelope_flags": list(HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS),
    "bootstrap_sha256": HELD_SMOKE_BOOTSTRAP_SHA256,
}
HELD_SMOKE_CHILD_CAPTURE_LIMIT_BYTES = 8 * 1024 * 1024
HELD_SMOKE_CHILD_TIMEOUT_LIMIT_SECONDS = 24 * 60 * 60
LOCK_METHOD = "fcntl.flock(LOCK_EX|LOCK_NB)_HELD_THROUGH_TERMINAL"
TERMINAL_PUBLICATION_METHOD = (
    "LINUX_XFS_O_TMPFILE_COMPLETE_FCHMOD0444_FSYNC_"
    "LINKAT_PROC_SELF_FD_AT_SYMLINK_FOLLOW_NOREPLACE_DIRFSYNC_V1"
)
TERMINAL_CANONICAL_VISIBILITY_RULE = (
    "CANONICAL_TERMINAL_ABSENT_UNTIL_COMPLETE_0444_FSYNCED_INODE_PUBLISH"
)
FILES_ONLY_DIGEST_ALGORITHM = (
    "sha256_sorted_relative_path_nul_sha256_nul_size_bytes_nul_mode_lf_v1"
)
STRUCTURAL_DIGEST_ALGORITHM = (
    "sha256_sorted_relative_path_nul_kind_nul_sha256_nul_size_bytes_nul_mode_lf_v1"
)
SCRATCH_INVENTORY_DIGEST_ALGORITHM = STRUCTURAL_DIGEST_ALGORITHM
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

V8_DIRECTORY_NAME = "post_stage06_release_chain_v8_prepared_20260822T142204Z"
V8_PREPARED_RECEIPT_SHA256 = (
    "8eb40f37057b1257c34e5f5a69c5fe394cb525c47158e2367262ec95eea24246"
)
V8_BUNDLE_MANIFEST_SHA256 = (
    "47c94860d2eae020b6f09e6e8ec7f79497d9dc48aeb4ae4579407b0bd0333e1f"
)
V8_SHA256_INDEX_SHA256 = (
    "9fbef6b48567d8055af152f5bd60821e31ef3d44e2013754ca929efb81504a5a"
)
V8_INDEXED_COUNT = 40
V8_TOP_LEVEL_COUNT = 41
V9_NEGATIVE_QA_SHA256 = {
    "bundle_manifest": "922e556401ec9a2520627e7a0795ff72ea9c1cee11446aad6bf406a00a15f452",
    "command_log": "48486c39c7f4613b149012e2021b206edeb506b913c877231753bf18d5b9d176",
    "attempt1_empty_stdout": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "attempt1_failure": "51bbd02eeac5a9d849e09277bd4e31bce6a0ff2b34f203d59e00a3636cc485de",
    "harness": "47951b6ec7b2c6a5389391340bc131ebbbf87be6c90c34a55d5fd53cbcc734ba",
    "output": "ab02284c8dcd72c5cd68cbee96ee7037a98f2044685c21b1501a8b2efe5b7dad",
    "receipt": "2fbeee49ac220b0faec1994f5b4d2a846e7e745ca0941d23aa351fee21e9cc97",
    "report": "cfff7d0388c25092a9a055fab0c16951b0f41e3ef3447c8ded66d5c43f94e08b",
    "closure": "871d3fd73403f5513c1393d1e7206dda510ff0587428be1be680820f7bc65185",
    "sha256_index": "5237b0d613170d357e1a2014318db4be078e8577bf6f33aa81f208a053386ebd",
}
V8_NEGATIVE_QA_SHA256 = {
    "bundle_manifest": "dcc965a626aa24681efd1e3d8715a45b4f0cdc471393040a2ea0ee40c147689d",
    "command_log": "2ed20737c8d33becc77169feaf07674b08bb2e341966ec915025db9858511c92",
    "attempt1_empty_stdout": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "attempt1_failure": "c1d59844d25133d7f82d7b2b234d8dc650f9ffd3df2d44333aefaf9ec669aa83",
    "attempt2_empty_stdout": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "attempt2_failure": "4dc5b036112a0ca15db56c0b51ebb2400eab3f807216cfa6ada922bb22e43d1c",
    "attempt3_failure": "9755a50bce2f80e44f5f89005758e1d787870d10193c666cc54bd1e167bdf5b8",
    "attempt3_output": "180661bfb27fb2963acbca291fc02965eca05cbe2cce2c2be557d6582de6f8d2",
    "harness": "13c86db155a29e6b4eeaea2828dd13da6f583a4eb87ef63da809017f2172376e",
    "output": "576ef34be32a1790537d0a5b1929cd7d5a8715af3edd0645aba13a64bd1d8d36",
    "receipt": "e0379c3295fe98afeee2a003a071298dc297cf6f25113488d986250a2fa29444",
    "report": "c8d1f3ae01b8461ddc24b2f7ae7e50f864ddad06d14aa181b831cf726a9fd5cc",
    "closure": "cb4bc3867edf0f51fa46d666e8cc76f6a510c6638fc268d80a51f04b05636128",
    "sha256_index": "071abb4150562d6dfb21d423a6bfe98d2b90e18fc078f537699d6f270e7c484b",
}
V7_NEGATIVE_QA_SHA256 = {
    "bundle_manifest": "c530a9815f7c3e8cc3b07c8cdb0ae8a4712ed9dccf8e75e278aa91e582c8591e",
    "log": "c892d4d14032b435f7b0a95b3e0eab27d7a243c8d956697eeef863896d74432b",
    "output": "7ba1cf084804c283aba69eae5f49500d6d625548a00828679e58640cfcee4702",
    "receipt": "5ac3fe473772f5ed21f3506669457fbec151909218814433359d3ee3c261595a",
    "report": "05012653344a2a61625084903b174f97e37435dc956ddcff8c0cb70f050101d1",
    "closure": "f761be56ed5ab361d639c95c719db0d3af0d9a3d0d9ea9b18de816f6abd1e013",
    "harness": "edf8db15b90cef36452f80d12b5afedf3db6a392b9126e5c555c4edf501e3d72",
    "sha256_index": "d0331e3babffa91162caa8e8f885b361fdb335b9cdfe8f238cbe5f1d9abf85a4",
}
ROOT_CHILDREN = {
    "bundle",
    "private_runtime_site_packages",
    "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json",
    "build_full_band_s4p_qa_v3.py",
    "FULL_BAND_V3_PANEL_SCHEMA_ADDENDUM.json",
    "EMX_RESULT_INTERFACE_TEMPLATE_FROZEN.json",
}
SUPPORT_SHA256 = {
    "build_full_band_s4p_qa_v3.py": (
        "ef04754f1552035543f3d9ac3eeab5ada3c0a8e411dfc4c87697ad898e1eaabe"
    ),
    "FULL_BAND_V3_PANEL_SCHEMA_ADDENDUM.json": (
        "fd2a4ad82bc7c25b6dd8af9bb690aeacd4d176cb926736b3ddcc2074d53b2268"
    ),
    "EMX_RESULT_INTERFACE_TEMPLATE_FROZEN.json": (
        "e481b7b1281c56dc05d99ff144f66f9fc3e3bc7f654717d4dafd98b519130b49"
    ),
}
BUILD_RECEIPT_TOP_KEYS = frozenset({
    "schema", "status", "created_utc", "decision_id", "authorization",
    "journal", "publication", "runtime", "support_files", "bound_v8",
    "source_runtime", "external_record_exclusions", "package_binding",
    "trusted_launch", "scope",
})
BUILD_RECEIPT_NESTED_KEYS = {
    "authorization": frozenset({"path", "sha256", "logical_builder_argv"}),
    "journal": frozenset({
        "directory", "directory_device", "directory_inode", "parent_path",
        "parent_device", "parent_inode", "begin_path", "begin_sha256",
        "commit_intent_path", "commit_intent_sha256", "terminal_path",
        "lock_path", "lock_device", "lock_inode", "lock_method",
        "terminal_publication_method", "terminal_canonical_visibility_rule",
    }),
    "publication": frozenset({
        "method", "final_root_path", "final_root_device", "final_root_inode",
        "staging_device", "staging_inode", "final_inode_equals_staging",
        "files_only_full_root_digest", "structural_full_root_digest",
    }),
    "runtime": frozenset({
        "manifest_path", "manifest_sha256", "files_only_runtime_root_digest",
        "private_root_path", "private_root_device", "private_root_inode",
        "files_only_private_root_digest", "structural_private_tree_digest",
        "bundle_root_path", "bundle_root_device", "bundle_root_inode",
    }),
    "bound_v8": frozenset({
        "bundle_path", "prepared_receipt_sha256", "bundle_manifest_sha256",
        "sha256_index_sha256", "top_level_count", "indexed_count",
    }),
    "source_runtime": frozenset({
        "python_path", "python_sha256", "site_packages_path",
        "site_packages_device", "site_packages_inode", "source_inventory_digest",
    }),
    "package_binding": frozenset({
        "v10_builder_sha256", "v10_test_sha256", "v10_smoke_sha256",
        "v10_smoke_test_sha256", "v10_smoke_bootstrap_sha256",
        "v10_smoke_bootstrap_size_bytes",
        "v10_bundle_manifest_sha256", "v10_sha256_index_sha256",
        "v10_prepared_receipt_sha256", "v10_independent_audit_receipt_sha256",
        "v9_negative_qa_bundle_manifest_sha256",
        "v9_negative_qa_command_log_sha256",
        "v9_negative_qa_attempt1_empty_stdout_sha256",
        "v9_negative_qa_attempt1_failure_sha256",
        "v9_negative_qa_harness_sha256",
        "v9_negative_qa_output_sha256",
        "v9_negative_qa_receipt_sha256",
        "v9_negative_qa_report_sha256",
        "v9_negative_qa_closure_sha256",
        "v9_negative_qa_sha256_index_sha256",
        "v8_negative_qa_bundle_manifest_sha256",
        "v8_negative_qa_command_log_sha256",
        "v8_negative_qa_attempt1_empty_stdout_sha256",
        "v8_negative_qa_attempt1_failure_sha256",
        "v8_negative_qa_attempt2_empty_stdout_sha256",
        "v8_negative_qa_attempt2_failure_sha256",
        "v8_negative_qa_attempt3_failure_sha256",
        "v8_negative_qa_attempt3_output_sha256",
        "v8_negative_qa_harness_sha256",
        "v8_negative_qa_output_sha256",
        "v8_negative_qa_receipt_sha256",
        "v8_negative_qa_report_sha256",
        "v8_negative_qa_closure_sha256",
        "v8_negative_qa_sha256_index_sha256",
        "v7_negative_qa_bundle_manifest_sha256",
        "v7_negative_qa_log_sha256", "v7_negative_qa_output_sha256",
        "v7_negative_qa_receipt_sha256", "v7_negative_qa_report_sha256",
        "v7_negative_qa_closure_sha256", "v7_negative_qa_harness_sha256",
        "v7_negative_qa_sha256_index_sha256",
        "v1_audit_receipt_sha256", "runtime_inventory_sha256",
    }),
    "trusted_launch": frozenset({
        "schema", "status", "method", "interpreter_fd", "builder_source_fd",
        "interpreter_proc_path", "builder_source_proc_path",
        "interpreter_fd_inheritable", "builder_source_fd_inheritable",
        "interpreter_identity", "builder_source_identity",
        "interpreter_sha256", "builder_source_sha256",
        "builder_original_evidence_path", "outer_launch_receipt_path",
        "outer_launch_receipt_sha256", "outer_process_argv",
        "outer_process_argv_sha256", "root_launch_authorization_path",
        "root_launch_authorization_sha256",
        "preflight_package_manifest_path",
        "preflight_package_manifest_sha256", "preflight_package_index_path",
        "preflight_package_index_sha256",
        "preflight_independent_audit_receipt_path",
        "preflight_independent_audit_receipt_sha256",
        "preflight_independent_audit_index_path",
        "preflight_independent_audit_index_sha256",
    }),
    "scope": frozenset({
        "result_free_transport_runtime_layout_only", "result_accessed",
        "signals_sent", "processes_inspected", "controller_or_outer_main_executed",
        "deployment_or_resume_executed", "smoke_executed", "linux_integration",
    }),
}
SUPPORT_FILE_RECEIPT_KEYS = frozenset({
    "path", "device", "inode", "sha256", "size_bytes",
})
ENVIRONMENT_KEYS = (
    "HOME",
    "MPLCONFIGDIR",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
)
EXTERNAL_RECORD_EXCLUSION_ENTRIES = [
    {"distribution": "fonttools", "relative_path": "../../../bin/fonttools"},
    {"distribution": "fonttools", "relative_path": "../../../bin/pyftmerge"},
    {"distribution": "fonttools", "relative_path": "../../../bin/pyftsubset"},
    {"distribution": "fonttools", "relative_path": "../../../bin/ttx"},
    {
        "distribution": "fonttools",
        "relative_path": "../../../share/man/man1/ttx.1",
    },
    {"distribution": "numpy", "relative_path": "../../../bin/f2py"},
    {"distribution": "numpy", "relative_path": "../../../bin/numpy-config"},
]

O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)

IN_ACCESS = 0x00000001
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_UNMOUNT = 0x00002000
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_EXCL_UNLINK = 0x04000000
INOTIFY_FAILURE_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_UNMOUNT
    | IN_Q_OVERFLOW
    | IN_IGNORED
)
INOTIFY_DIRECTORY_WATCH_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_ONLYDIR
    | IN_DONT_FOLLOW
    | IN_EXCL_UNLINK
)
INOTIFY_REGULAR_FILE_WATCH_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_UNMOUNT
)
INOTIFY_EVENT_HEADER = struct.Struct("iIII")

ELF_MAGIC = b"\x7fELF"
ELFCLASS64 = 2
ELFDATA2LSB = 1
PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_STRSZ = 10
DT_SONAME = 14
DT_RPATH = 15
DT_RUNPATH = 29
PROC_MAP_LINE_RE = re.compile(
    r"^([0-9a-f]+)-([0-9a-f]+)\s+([rwxps-]{4})\s+([0-9a-f]+)\s+"
    r"([0-9a-f]+):([0-9a-f]+)\s+(\d+)(?:\s+(.*))?$"
)
SYSTEM_DSO_ROOTS = (
    "/lib",
    "/lib64",
    "/usr/lib",
    "/usr/lib64",
)


class SmokeError(RuntimeError):
    """Fail-closed contract error."""


class HeldSmokeChildError(SmokeError):
    """Bounded failure from the one child created by the preflight helper."""

    def __init__(
        self, reason: str, *, returncode: int | None = None,
        stdout: bytes = b"", stderr: bytes = b""
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class DuplicateKeyError(ValueError):
    """Strict JSON duplicate-key error."""


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _reject_json_types_and_surrogates(value: Any, label: str) -> None:
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise SmokeError(f"{label}: non-string object key")
            _reject_json_types_and_surrogates(key, f"{label}.<key>")
            _reject_json_types_and_surrogates(item, f"{label}.{key}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _reject_json_types_and_surrogates(item, f"{label}[{index}]")
        return
    if type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise SmokeError(f"{label}: Unicode surrogate forbidden")
        if "\x00" in value:
            raise SmokeError(f"{label}: NUL forbidden")
        return
    if type(value) in {bool, int}:
        return
    # No schema used by this smoke needs float or null.  Rejecting both also
    # prevents int/float and nullable-field type confusion.
    raise SmokeError(f"{label}: unsupported JSON type {type(value).__name__}")


def strict_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SmokeError(f"{label}: UTF-8 BOM forbidden")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        raise SmokeError(f"{label}: strict JSON parse failed: {exc}") from exc
    if type(value) is not dict:
        raise SmokeError(f"{label}: top-level JSON object required")
    _reject_json_types_and_surrogates(value, label)
    return value


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise SmokeError(f"{label}: object required")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise SmokeError(f"{label}: exact keys mismatch missing={missing}, extra={extra}")
    return value


def exact_string(value: Any, label: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise SmokeError(f"{label}: exact string required")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise SmokeError(f"{label}: control character forbidden")
    return value


def exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise SmokeError(f"{label}: exact boolean {expected!r} required")


def exact_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise SmokeError(f"{label}: exact nonnegative integer required")
    return value


def exact_sha(value: Any, label: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise SmokeError(f"{label}: lowercase SHA-256 required")
    return value


def exact_string_list(value: Any, label: str, *, unique: bool = False) -> list[str]:
    if type(value) is not list:
        raise SmokeError(f"{label}: list required")
    result = [exact_string(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if unique and len(result) != len(set(result)):
        raise SmokeError(f"{label}: duplicate list member")
    return result


def exact_process_argv(value: Any, label: str) -> list[str]:
    if type(value) is not list or not value:
        raise SmokeError(f"{label}: nonempty list required")
    result: list[str] = []
    for index, item in enumerate(value):
        if type(item) is not str or not item or "\x00" in item:
            raise SmokeError(f"{label}[{index}]: nonempty NUL-free string required")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
            raise SmokeError(f"{label}[{index}]: Unicode surrogate forbidden")
        result.append(item)
    return result


def exact_identity(value: Any, label: str) -> dict[str, int]:
    item = exact_object(value, {"device", "inode"}, label)
    return {
        "device": exact_int(item["device"], f"{label}.device"),
        "inode": exact_int(item["inode"], f"{label}.inode"),
    }


def canonical_absolute_path(value: Any, label: str) -> str:
    text = exact_string(value, label)
    pure = PurePosixPath(text)
    if not pure.is_absolute() or text != os.fspath(pure):
        raise SmokeError(f"{label}: canonical absolute POSIX path required")
    if any(part in {"", ".", ".."} for part in pure.parts[1:]):
        raise SmokeError(f"{label}: unsafe path component")
    return text


def exact_held_smoke_source_identity(
    value: Any, label: str = "held smoke source identity"
) -> dict[str, Any]:
    item = exact_object(
        value,
        {
            "device",
            "inode",
            "size_bytes",
            "mtime_ns",
            "ctime_ns",
            "mode",
            "nlink",
        },
        label,
    )
    result = {
        name: exact_int(item[name], f"{label}.{name}")
        for name in (
            "device",
            "inode",
            "size_bytes",
            "mtime_ns",
            "ctime_ns",
            "nlink",
        )
    }
    if exact_string(item["mode"], f"{label}.mode") != "0444":
        raise SmokeError(f"{label}.mode: exact 0444 required")
    if result["nlink"] != 1:
        raise SmokeError(f"{label}.nlink: exact 1 required")
    return {**result, "mode": "0444"}


def held_smoke_authorization_binding(
    source_identity: Mapping[str, Any],
    source_sha256: str,
    original_evidence_path: str,
) -> dict[str, Any]:
    """Build the exact dynamic authorization member for a held smoke source."""

    identity = exact_held_smoke_source_identity(
        dict(source_identity), "held smoke source identity"
    )
    return {
        "contract": dict(HELD_SMOKE_BOOTSTRAP_CONTRACT),
        "smoke_source_identity": identity,
        "smoke_source_sha256": exact_sha(source_sha256, "held smoke source SHA"),
        "original_smoke_evidence_path": canonical_absolute_path(
            original_evidence_path, "original smoke evidence path"
        ),
    }


def build_held_smoke_argv(
    source_identity: Mapping[str, Any],
    source_sha256: str,
    original_evidence_path: str,
    smoke_cli: Sequence[str],
) -> list[str]:
    """Return the one authorized FD197 ``-c`` command; it executes no path code."""

    binding = held_smoke_authorization_binding(
        source_identity, source_sha256, original_evidence_path
    )
    identity = binding["smoke_source_identity"]
    if type(smoke_cli) not in {list, tuple} or not smoke_cli:
        raise SmokeError("smoke CLI must be a nonempty list/tuple")
    cli = [exact_string(item, f"smoke CLI[{index}]") for index, item in enumerate(smoke_cli)]
    if len(cli) < 4 or cli[0] != "--smoke-authorization" or cli[2] != (
        "--trusted-smoke-authorization-sha256"
    ):
        raise SmokeError("smoke CLI exact authorization prefix mismatch")
    if any(
        token == flag or token.startswith(flag + "=")
        for token in cli
        for flag in HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS
    ):
        raise SmokeError("smoke CLI repeats a bootstrap envelope flag")
    values = (
        str(SMOKE_SOURCE_FD),
        str(identity["device"]),
        str(identity["inode"]),
        str(identity["size_bytes"]),
        str(identity["mtime_ns"]),
        str(identity["ctime_ns"]),
        identity["mode"],
        str(identity["nlink"]),
        binding["smoke_source_sha256"],
        binding["original_smoke_evidence_path"],
        HELD_SMOKE_BOOTSTRAP_SHA256,
    )
    envelope = [
        member
        for pair in zip(HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS, values)
        for member in pair
    ]
    return [
        f"/proc/self/fd/{INTERPRETER_FD}",
        "-I",
        "-B",
        "-S",
        "-c",
        HELD_SMOKE_BOOTSTRAP_TEXT,
        *envelope,
        *cli,
    ]


def _owned_child_kill_and_wait(process: Any) -> None:
    """Kill/reap only the exact child object created by this helper."""

    try:
        running = process.poll() is None
    except BaseException:
        running = True
    if running:
        process.kill()
    process.wait()


def _capture_owned_child_bounded(
    process: Any,
    *,
    timeout_seconds: int,
    capture_limit_bytes: int,
    selector_factory: Any = selectors.DefaultSelector,
    monotonic: Any = time.monotonic,
) -> tuple[int, bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise HeldSmokeChildError("child pipes are absent")
    selector = selector_factory()
    buffers: dict[str, bytearray] = {
        "stdout": bytearray(), "stderr": bytearray()
    }
    streams = {
        process.stdout: "stdout",
        process.stderr: "stderr",
    }
    deadline = monotonic() + timeout_seconds
    try:
        for stream, label in streams.items():
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise HeldSmokeChildError("held smoke child timed out")
            events = selector.select(remaining)
            if not events:
                raise HeldSmokeChildError("held smoke child timed out")
            for key, _mask in events:
                try:
                    block = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = buffers[key.data]
                target.extend(block)
                if len(target) > capture_limit_bytes:
                    raise HeldSmokeChildError(
                        f"held smoke child {key.data} exceeded capture limit"
                    )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise HeldSmokeChildError("held smoke child timed out before wait")
        returncode = process.wait(timeout=remaining)
        return returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except BaseException:
        _owned_child_kill_and_wait(process)
        raise
    finally:
        selector.close()
        for stream in streams:
            try:
                stream.close()
            except BaseException:
                pass


def require_readonly_fd(fd: int, label: str) -> int:
    try:
        status_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError as exc:
        raise SmokeError(f"{label} FD is absent: {exc}") from exc
    if status_flags & os.O_ACCMODE != os.O_RDONLY:
        raise SmokeError(f"{label} FD is not O_RDONLY")
    return status_flags


def run_held_smoke_child(
    source_identity: Mapping[str, Any],
    source_sha256: str,
    original_evidence_path: str,
    smoke_cli: Sequence[str],
    *,
    timeout_seconds: int,
    capture_limit_bytes: int = HELD_SMOKE_CHILD_CAPTURE_LIMIT_BYTES,
    environment: Mapping[str, str] | None = None,
    _popen_factory: Any = None,
    _selector_factory: Any = selectors.DefaultSelector,
    _monotonic: Any = time.monotonic,
) -> dict[str, Any]:
    """Spawn the one smoke child from held FD197/198 and capture it boundedly."""

    if type(timeout_seconds) is not int or not (
        1 <= timeout_seconds <= HELD_SMOKE_CHILD_TIMEOUT_LIMIT_SECONDS
    ):
        raise SmokeError("timeout_seconds must be an exact bounded positive integer")
    if type(capture_limit_bytes) is not int or not (
        1 <= capture_limit_bytes <= HELD_SMOKE_CHILD_CAPTURE_LIMIT_BYTES
    ):
        raise SmokeError("capture_limit_bytes must be an exact bounded positive integer")
    identity = exact_held_smoke_source_identity(
        dict(source_identity), "held smoke child source identity"
    )
    source_sha = exact_sha(source_sha256, "held smoke child source SHA")
    command = build_held_smoke_argv(
        identity, source_sha, original_evidence_path, smoke_cli
    )
    cli = command[6 + 2 * len(HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS):]
    if cli.count("--expected-python-sha256") != 1:
        raise SmokeError("smoke CLI needs one separate expected Python SHA")
    python_sha_index = cli.index("--expected-python-sha256")
    if python_sha_index + 1 >= len(cli):
        raise SmokeError("smoke CLI expected Python SHA value is absent")
    expected_python_sha = exact_sha(
        cli[python_sha_index + 1], "smoke CLI expected Python SHA"
    )
    for token in cli:
        if token.startswith("--expected-python-sha256="):
            raise SmokeError("inline expected Python SHA form is forbidden")

    if environment is None:
        child_environment = None
    else:
        if type(environment) is not dict:
            raise SmokeError("child environment must be an exact dict[str,str]")
        child_environment: dict[str, str] = {}
        for key, value in environment.items():
            if (
                type(key) is not str
                or type(value) is not str
                or not key
                or "=" in key
                or "\x00" in key
                or "\x00" in value
            ):
                raise SmokeError("child environment contains an invalid key/value")
            child_environment[key] = value

    fd_snapshots: dict[int, tuple[tuple[int, ...], int, int]] = {}
    for fd, label in (
        (INTERPRETER_FD, "interpreter"), (SMOKE_SOURCE_FD, "smoke source")
    ):
        try:
            info = os.fstat(fd)
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            status_flags = require_readonly_fd(fd, f"held {label} before spawn")
        except OSError as exc:
            raise SmokeError(f"held {label} FD is absent before spawn: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise SmokeError(f"held {label} FD is not regular before spawn")
        fd_snapshots[fd] = (_stable_stat_tuple(info), flags, status_flags)
    if _stat_full_file_identity(os.fstat(SMOKE_SOURCE_FD)) != identity:
        raise SmokeError("held FD198 identity differs from child envelope")
    if digest_fd(SMOKE_SOURCE_FD, "held child source pre-spawn")[0] != source_sha:
        raise SmokeError("held FD198 SHA differs from child envelope")
    if digest_fd(INTERPRETER_FD, "held child interpreter pre-spawn")[0] != (
        expected_python_sha
    ):
        raise SmokeError("held FD197 SHA differs from smoke CLI")

    if _popen_factory is None:
        import subprocess

        popen_factory = subprocess.Popen
        devnull = subprocess.DEVNULL
        pipe = subprocess.PIPE
    else:
        popen_factory = _popen_factory
        devnull = -3
        pipe = -1
    process = None
    try:
        try:
            process = popen_factory(
                command,
                executable=f"/proc/self/fd/{INTERPRETER_FD}",
                stdin=devnull,
                stdout=pipe,
                stderr=pipe,
                close_fds=True,
                pass_fds=(INTERPRETER_FD, SMOKE_SOURCE_FD),
                shell=False,
                env=child_environment,
                text=False,
                start_new_session=False,
            )
        except BaseException as exc:
            raise HeldSmokeChildError(
                f"held smoke child spawn failed: {type(exc).__name__}: {exc}"
            ) from exc
        returncode, stdout, stderr = _capture_owned_child_bounded(
            process,
            timeout_seconds=timeout_seconds,
            capture_limit_bytes=capture_limit_bytes,
            selector_factory=_selector_factory,
            monotonic=_monotonic,
        )
        if returncode != 0:
            raise HeldSmokeChildError(
                "held smoke child returned nonzero",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return {
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "argv_sha256": hashlib.sha256(
                json.dumps(
                    command, ensure_ascii=False, separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
            "executable": f"/proc/self/fd/{INTERPRETER_FD}",
            "pass_fds": [INTERPRETER_FD, SMOKE_SOURCE_FD],
            "close_fds": True,
            "capture_limit_bytes": capture_limit_bytes,
            "timeout_seconds": timeout_seconds,
        }
    finally:
        for fd, (
            expected_stat, expected_flags, expected_status_flags
        ) in fd_snapshots.items():
            try:
                current_stat = _stable_stat_tuple(os.fstat(fd))
                current_flags = fcntl.fcntl(fd, fcntl.F_GETFD)
                current_status_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            except OSError as exc:
                raise SmokeError(f"held FD{fd} disappeared after child: {exc}") from exc
            if (
                current_stat != expected_stat
                or current_flags != expected_flags
                or current_status_flags != expected_status_flags
                or current_status_flags & os.O_ACCMODE != os.O_RDONLY
            ):
                raise SmokeError(f"held FD{fd} identity/flags changed across child")


def safe_relative_path(value: Any, label: str) -> str:
    text = exact_string(value, label)
    pure = PurePosixPath(text)
    if pure.is_absolute() or text != os.fspath(pure) or not pure.parts:
        raise SmokeError(f"{label}: canonical relative POSIX path required")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise SmokeError(f"{label}: unsafe relative component")
    return text


def fstat_identity(info: os.stat_result) -> dict[str, int]:
    return {"device": info.st_dev, "inode": info.st_ino}


def same_identity(info: os.stat_result, expected: Mapping[str, int]) -> bool:
    return info.st_dev == expected["device"] and info.st_ino == expected["inode"]


def _require_platform_fd_features() -> None:
    if not sys.platform.startswith("linux"):
        raise SmokeError("Linux is required")
    if not O_DIRECTORY or not O_NOFOLLOW:
        raise SmokeError("O_DIRECTORY and O_NOFOLLOW are required")
    if not Path("/proc/self/fd").is_dir() or not Path("/proc/self/cmdline").is_file():
        raise SmokeError("Linux /proc/self/fd and /proc/self/cmdline are required")


def _open_directory_component(parent_fd: int, name: str, label: str) -> int:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise SmokeError(f"{label}: unsafe directory component")
    try:
        fd = os.open(
            name,
            os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise SmokeError(f"{label}: nofollow directory open failed: {exc}") from exc
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise SmokeError(f"{label}: directory required")
    return fd


def open_absolute_parent(path: str, label: str) -> tuple[int, str]:
    canonical = canonical_absolute_path(path, label)
    parts = PurePosixPath(canonical).parts
    if len(parts) < 2:
        raise SmokeError(f"{label}: filesystem root itself is forbidden")
    fd = os.open("/", os.O_RDONLY | O_DIRECTORY | O_CLOEXEC)
    try:
        for index, component in enumerate(parts[1:-1]):
            child = _open_directory_component(fd, component, f"{label}[{index}]")
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def open_absolute_directory(path: str, label: str) -> tuple[int, int, str]:
    parent_fd, name = open_absolute_parent(path, label)
    try:
        directory_fd = _open_directory_component(parent_fd, name, label)
    except BaseException:
        os.close(parent_fd)
        raise
    return parent_fd, directory_fd, name


def open_absolute_regular(path: str, label: str) -> tuple[int, int, str]:
    parent_fd, name = open_absolute_parent(path, label)
    try:
        fd = os.open(
            name,
            os.O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        os.close(parent_fd)
        raise SmokeError(f"{label}: nofollow file open failed: {exc}") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        os.close(parent_fd)
        raise SmokeError(f"{label}: single-link regular file required")
    return parent_fd, fd, name


def open_child(parent_fd: int, name: str, label: str, *, directory: bool) -> int:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise SmokeError(f"{label}: unsafe child name")
    flags = os.O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC
    if directory:
        flags |= O_DIRECTORY
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise SmokeError(f"{label}: nofollow child open failed: {exc}") from exc
    info = os.fstat(fd)
    if directory and not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise SmokeError(f"{label}: directory required")
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        os.close(fd)
        raise SmokeError(f"{label}: single-link regular file required")
    return fd


def fresh_directory_cursor(held_fd: int, label: str) -> int:
    """Open a fresh OFD for ``held_fd/.`` and pin it to the held inode.

    Some network/tmpfs directory open-file-descriptions can retain a stale
    enumeration cursor.  Every inventory/list operation therefore receives a
    newly opened cursor while the original authority FD stays held.
    """

    held = os.fstat(held_fd)
    if not stat.S_ISDIR(held.st_mode):
        raise SmokeError(f"{label}: held object is not a directory")
    try:
        cursor = os.open(
            ".",
            os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
            dir_fd=held_fd,
        )
    except OSError as exc:
        raise SmokeError(f"{label}: fresh directory cursor open failed: {exc}") from exc
    current = os.fstat(cursor)
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_dev != held.st_dev
        or current.st_ino != held.st_ino
    ):
        os.close(cursor)
        raise SmokeError(f"{label}: fresh directory cursor identity mismatch")
    return cursor


def fresh_directory_names(held_fd: int, label: str) -> list[str]:
    cursor = fresh_directory_cursor(held_fd, label)
    try:
        names = sorted(os.listdir(cursor))
    except OSError as exc:
        raise SmokeError(f"{label}: fresh directory listing failed: {exc}") from exc
    finally:
        os.close(cursor)
    if len(names) != len(set(names)):
        raise SmokeError(f"{label}: duplicate directory entry")
    for name in names:
        if not name or name in {".", ".."} or "/" in name or "\x00" in name:
            raise SmokeError(f"{label}: unsafe directory entry")
    return names


def open_relative_regular(root_fd: int, relative: str, label: str) -> int:
    safe = safe_relative_path(relative, label)
    components = PurePosixPath(safe).parts
    current = os.dup(root_fd)
    try:
        for index, component in enumerate(components[:-1]):
            child = _open_directory_component(current, component, f"{label}[{index}]")
            os.close(current)
            current = child
        fd = open_child(current, components[-1], label, directory=False)
    finally:
        os.close(current)
    return fd


def open_relative_directory(root_fd: int, relative: str, label: str) -> int:
    safe = safe_relative_path(relative, label)
    current = os.dup(root_fd)
    try:
        for index, component in enumerate(PurePosixPath(safe).parts):
            child = _open_directory_component(current, component, f"{label}[{index}]")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _stable_stat_tuple(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_fd_bytes(fd: int, label: str, *, maximum_size: int) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SmokeError(f"{label}: single-link regular file required")
    if before.st_size < 0 or before.st_size > maximum_size:
        raise SmokeError(f"{label}: file size outside allowed bound")
    chunks: list[bytes] = []
    offset = 0
    while offset < before.st_size:
        block = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
        if not block:
            raise SmokeError(f"{label}: premature EOF")
        chunks.append(block)
        offset += len(block)
    if os.pread(fd, 1, offset):
        raise SmokeError(f"{label}: file grew during read")
    after = os.fstat(fd)
    if _stable_stat_tuple(before) != _stable_stat_tuple(after):
        raise SmokeError(f"{label}: identity/bytes changed during read")
    data = b"".join(chunks)
    if len(data) != before.st_size:
        raise SmokeError(f"{label}: byte count mismatch")
    return data


def digest_fd(fd: int, label: str) -> tuple[str, int]:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SmokeError(f"{label}: single-link regular file required")
    value = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        block = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
        if not block:
            raise SmokeError(f"{label}: premature EOF")
        value.update(block)
        offset += len(block)
    if os.pread(fd, 1, offset):
        raise SmokeError(f"{label}: file grew during digest")
    after = os.fstat(fd)
    if _stable_stat_tuple(before) != _stable_stat_tuple(after):
        raise SmokeError(f"{label}: identity/bytes changed during digest")
    return value.hexdigest(), before.st_size


def _tree_record(
    relative_path: str,
    kind: str,
    mode: str,
    *,
    digest: str = "",
    size: int = 0,
) -> dict[str, Any]:
    return {
        "relative_path": relative_path,
        "kind": kind,
        "sha256": digest,
        "size_bytes": size,
        "mode": mode,
    }


def inventory_tree(
    directory_fd: int,
    label: str,
    *,
    include_root: bool,
    require_frozen_modes: bool,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root_info = os.fstat(directory_fd)
    if not stat.S_ISDIR(root_info.st_mode):
        raise SmokeError(f"{label}: held root is not a directory")
    if require_frozen_modes and stat.S_IMODE(root_info.st_mode) != 0o555:
        raise SmokeError(f"{label}: root directory mode must be 0555")
    if include_root:
        records.append(
            _tree_record(
                ".",
                "directory",
                f"0{stat.S_IMODE(root_info.st_mode):03o}",
            )
        )

    def recurse(fd: int, prefix: str) -> None:
        names_before = fresh_directory_names(fd, f"{label}:{prefix or '.'}:before")
        cursor = fresh_directory_cursor(fd, f"{label}:{prefix or '.'}:walk")
        try:
            for name in names_before:
                relative = f"{prefix}/{name}" if prefix else name
                try:
                    child = os.open(
                        name,
                        os.O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC,
                        dir_fd=cursor,
                    )
                except OSError as exc:
                    raise SmokeError(f"{label}: nofollow tree open failed at {relative}: {exc}") from exc
                try:
                    info = os.fstat(child)
                    mode = f"{stat.S_IMODE(info.st_mode):04o}"
                    if stat.S_ISDIR(info.st_mode):
                        if require_frozen_modes and stat.S_IMODE(info.st_mode) != 0o555:
                            raise SmokeError(f"{label}: directory mode mismatch at {relative}")
                        records.append(_tree_record(relative, "directory", mode))
                        recurse(child, relative)
                    elif stat.S_ISREG(info.st_mode):
                        if info.st_nlink != 1:
                            raise SmokeError(f"{label}: hard-linked file forbidden at {relative}")
                        if require_frozen_modes and stat.S_IMODE(info.st_mode) != 0o444:
                            raise SmokeError(f"{label}: file mode mismatch at {relative}")
                        digest, size = digest_fd(child, f"{label}:{relative}")
                        records.append(
                            _tree_record(relative, "regular", mode, digest=digest, size=size)
                        )
                    else:
                        raise SmokeError(f"{label}: symlink/special member forbidden at {relative}")
                finally:
                    os.close(child)
        finally:
            os.close(cursor)
        names_after = fresh_directory_names(fd, f"{label}:{prefix or '.'}:after")
        if names_before != names_after:
            raise SmokeError(f"{label}: directory entries changed during inventory")

    recurse(directory_fd, "")
    records.sort(key=lambda item: item["relative_path"])
    if len(records) != len({item["relative_path"] for item in records}):
        raise SmokeError(f"{label}: duplicate inventory path")
    return records


def structural_digest(records: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"{item['relative_path']}\0{item['kind']}\0{item['sha256']}\0"
        f"{item['size_bytes']}\0{item['mode']}\n"
        for item in records
    ]
    return hashlib.sha256("".join(sorted(lines)).encode("utf-8")).hexdigest()


def files_only_records(tree_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": item["relative_path"],
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
            "mode": item["mode"],
        }
        for item in tree_records
        if item["kind"] == "regular"
    ]


def files_only_digest(records: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"{item['relative_path']}\0{item['sha256']}\0{item['size_bytes']}\0{item['mode']}\n"
        for item in records
    ]
    return hashlib.sha256("".join(sorted(lines)).encode("utf-8")).hexdigest()


def scratch_delta(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    before_map = {item["relative_path"]: dict(item) for item in before}
    after_map = {item["relative_path"]: dict(item) for item in after}
    return {
        "added": [after_map[name] for name in sorted(set(after_map) - set(before_map))],
        "removed": [before_map[name] for name in sorted(set(before_map) - set(after_map))],
        "modified": [
            {"before": before_map[name], "after": after_map[name]}
            for name in sorted(set(before_map) & set(after_map))
            if before_map[name] != after_map[name]
        ],
    }


def read_proc_cmdline() -> list[str]:
    fd = os.open("/proc/self/cmdline", os.O_RDONLY | O_CLOEXEC)
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    if not raw.endswith(b"\0"):
        raise SmokeError("/proc/self/cmdline lacks terminal NUL")
    pieces = raw[:-1].split(b"\0")
    if not pieces or any(not piece for piece in pieces):
        raise SmokeError("/proc/self/cmdline contains empty argv member")
    try:
        return [piece.decode("utf-8", errors="strict") for piece in pieces]
    except UnicodeDecodeError as exc:
        raise SmokeError("/proc/self/cmdline is not strict UTF-8") from exc


def _parse_cli(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--smoke-authorization", required=True)
    parser.add_argument("--trusted-smoke-authorization-sha256", required=True)
    parser.add_argument("--build-pass-receipt", required=True)
    parser.add_argument("--trusted-build-pass-receipt-sha256", required=True)
    parser.add_argument("--expected-build-authorization-sha256", required=True)
    parser.add_argument("--expected-build-commit-intent-sha256", required=True)
    parser.add_argument("--final-root", required=True)
    parser.add_argument("--expected-final-root-device", type=int, required=True)
    parser.add_argument("--expected-final-root-inode", type=int, required=True)
    parser.add_argument("--expected-runtime-manifest-sha256", required=True)
    parser.add_argument("--expected-files-only-runtime-root-digest", required=True)
    parser.add_argument("--expected-files-only-private-root-digest", required=True)
    parser.add_argument("--expected-structural-private-tree-digest", required=True)
    parser.add_argument("--expected-files-only-full-root-digest", required=True)
    parser.add_argument("--expected-structural-full-root-digest", required=True)
    parser.add_argument("--scratch-dir", required=True)
    parser.add_argument("--expected-scratch-device", type=int, required=True)
    parser.add_argument("--expected-scratch-inode", type=int, required=True)
    parser.add_argument("--expected-empty-scratch-digest", required=True)
    parser.add_argument("--expected-python-sha256", required=True)
    parser.add_argument("--expected-smoke-script-sha256", required=True)
    parser.add_argument("--execute", choices=[CAPABILITY], required=True)
    security_options = tuple(action.option_strings[0] for action in parser._actions if action.required)
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw:
        parser.error("bare -- is forbidden")
    for option in security_options:
        if any(token.startswith(option + "=") for token in raw):
            parser.error(f"inline option form forbidden: {option}")
        if raw.count(option) != 1:
            parser.error(f"exactly one separate-token {option} is required")
    args = parser.parse_args(raw)
    for name in (
        "expected_final_root_device",
        "expected_final_root_inode",
        "expected_scratch_device",
        "expected_scratch_inode",
    ):
        if getattr(args, name) < 0:
            parser.error(f"{name} must be nonnegative")
    for name in (
        "trusted_smoke_authorization_sha256",
        "trusted_build_pass_receipt_sha256",
        "expected_build_authorization_sha256",
        "expected_build_commit_intent_sha256",
        "expected_runtime_manifest_sha256",
        "expected_files_only_runtime_root_digest",
        "expected_files_only_private_root_digest",
        "expected_structural_private_tree_digest",
        "expected_files_only_full_root_digest",
        "expected_structural_full_root_digest",
        "expected_empty_scratch_digest",
        "expected_python_sha256",
        "expected_smoke_script_sha256",
    ):
        if not SHA_RE.fullmatch(getattr(args, name)):
            parser.error(f"{name} must be lowercase SHA-256")
    canonical_absolute_path(args.smoke_authorization, "--smoke-authorization")
    canonical_absolute_path(args.build_pass_receipt, "--build-pass-receipt")
    canonical_absolute_path(args.final_root, "--final-root")
    canonical_absolute_path(args.scratch_dir, "--scratch-dir")
    return args


def validate_smoke_authorization(
    authorization: dict[str, Any],
    args: argparse.Namespace,
    actual_cmdline: list[str],
    bootstrap_context: Mapping[str, Any],
    smoke_argv: Sequence[str],
) -> dict[str, Any]:
    top = exact_object(
        authorization,
        {
            "schema",
            "status",
            "decision_id",
            "scope",
            "authority",
            "paths",
            "identities",
            "expected",
            "bound_v8",
            "held_byte_bootstrap",
            "exact_process_argv_template",
            "exact_isolation_flags",
            "imports_exact",
            "environment_policy",
            "capability",
        },
        "authorization",
    )
    if top["schema"] != AUTHORIZATION_SCHEMA or top["status"] != AUTHORIZATION_STATUS:
        raise SmokeError("authorization schema/status mismatch")
    exact_string(top["decision_id"], "authorization.decision_id")
    if top["scope"] != "RESULT_FREE_RUNTIME_LAYOUT_SMOKE_ONLY":
        raise SmokeError("authorization scope mismatch")
    authority = exact_object(
        top["authority"],
        {
            "runtime_layout_smoke_authorized",
            "scratch_write_authorized",
            "root_write_authorized",
            "transport_or_build_authorized",
            "controller_or_outer_main_authorized",
            "result_access_authorized",
            "signals_authorized",
            "deployment_or_resume_authorized",
        },
        "authorization.authority",
    )
    exact_bool(authority["runtime_layout_smoke_authorized"], True, "authorization.authority.runtime_layout_smoke_authorized")
    exact_bool(authority["scratch_write_authorized"], True, "authorization.authority.scratch_write_authorized")
    for name in (
        "root_write_authorized",
        "transport_or_build_authorized",
        "controller_or_outer_main_authorized",
        "result_access_authorized",
        "signals_authorized",
        "deployment_or_resume_authorized",
    ):
        exact_bool(authority[name], False, f"authorization.authority.{name}")

    paths = exact_object(
        top["paths"],
        {
            "smoke_authorization",
            "build_pass_receipt",
            "final_root",
            "scratch_dir",
            "source_python",
            "smoke_script",
        },
        "authorization.paths",
    )
    for name in paths:
        paths[name] = canonical_absolute_path(paths[name], f"authorization.paths.{name}")
    expected_paths = {
        "smoke_authorization": args.smoke_authorization,
        "build_pass_receipt": args.build_pass_receipt,
        "final_root": args.final_root,
        "scratch_dir": args.scratch_dir,
    }
    for name, expected_path in expected_paths.items():
        if paths[name] != expected_path:
            raise SmokeError(f"authorization path/CLI mismatch: {name}")

    identities = exact_object(
        top["identities"], {"final_root", "scratch"}, "authorization.identities"
    )
    root_identity = exact_identity(identities["final_root"], "authorization.identities.final_root")
    scratch_identity = exact_identity(identities["scratch"], "authorization.identities.scratch")
    if root_identity != {
        "device": args.expected_final_root_device,
        "inode": args.expected_final_root_inode,
    }:
        raise SmokeError("authorization final ROOT identity/CLI mismatch")
    if scratch_identity != {
        "device": args.expected_scratch_device,
        "inode": args.expected_scratch_inode,
    }:
        raise SmokeError("authorization scratch identity/CLI mismatch")

    expected = exact_object(
        top["expected"],
        {
            "build_pass_receipt_sha256",
            "build_authorization_sha256",
            "build_commit_intent_sha256",
            "runtime_manifest_sha256",
            "files_only_runtime_root_digest",
            "files_only_private_root_digest",
            "structural_private_tree_digest",
            "files_only_full_root_digest",
            "structural_full_root_digest",
            "empty_scratch_inventory_digest",
            "source_python_sha256",
            "smoke_script_sha256",
        },
        "authorization.expected",
    )
    expected_cli = {
        "build_pass_receipt_sha256": args.trusted_build_pass_receipt_sha256,
        "build_authorization_sha256": args.expected_build_authorization_sha256,
        "build_commit_intent_sha256": args.expected_build_commit_intent_sha256,
        "runtime_manifest_sha256": args.expected_runtime_manifest_sha256,
        "files_only_runtime_root_digest": args.expected_files_only_runtime_root_digest,
        "files_only_private_root_digest": args.expected_files_only_private_root_digest,
        "structural_private_tree_digest": args.expected_structural_private_tree_digest,
        "files_only_full_root_digest": args.expected_files_only_full_root_digest,
        "structural_full_root_digest": args.expected_structural_full_root_digest,
        "empty_scratch_inventory_digest": args.expected_empty_scratch_digest,
        "source_python_sha256": args.expected_python_sha256,
        "smoke_script_sha256": args.expected_smoke_script_sha256,
    }
    for name, cli_value in expected_cli.items():
        value = exact_sha(expected[name], f"authorization.expected.{name}")
        if value != cli_value:
            raise SmokeError(f"authorization expected/CLI mismatch: {name}")
    if expected["empty_scratch_inventory_digest"] != EMPTY_SHA256:
        raise SmokeError("authorization must bind the exact empty scratch digest")

    held_byte_bootstrap = exact_object(
        top["held_byte_bootstrap"],
        {
            "contract",
            "smoke_source_identity",
            "smoke_source_sha256",
            "original_smoke_evidence_path",
        },
        "authorization.held_byte_bootstrap",
    )
    contract = exact_object(
        held_byte_bootstrap["contract"],
        set(HELD_SMOKE_BOOTSTRAP_CONTRACT),
        "authorization.held_byte_bootstrap.contract",
    )
    if contract != HELD_SMOKE_BOOTSTRAP_CONTRACT:
        raise SmokeError("authorization frozen held bootstrap contract mismatch")
    source_identity = exact_held_smoke_source_identity(
        held_byte_bootstrap["smoke_source_identity"],
        "authorization.held_byte_bootstrap.smoke_source_identity",
    )
    if source_identity != bootstrap_context["smoke_source_identity"]:
        raise SmokeError("authorization/bootstrap smoke source identity mismatch")
    if exact_sha(
        held_byte_bootstrap["smoke_source_sha256"],
        "authorization.held_byte_bootstrap.smoke_source_sha256",
    ) != bootstrap_context["smoke_source_sha256"]:
        raise SmokeError("authorization/bootstrap smoke source SHA mismatch")
    if canonical_absolute_path(
        held_byte_bootstrap["original_smoke_evidence_path"],
        "authorization.held_byte_bootstrap.original_smoke_evidence_path",
    ) != bootstrap_context["original_smoke_evidence_path"]:
        raise SmokeError("authorization/bootstrap original smoke evidence path mismatch")

    bound_v8 = exact_object(
        top["bound_v8"],
        {
            "directory_name",
            "prepared_receipt_sha256",
            "bundle_manifest_sha256",
            "sha256_index_sha256",
            "top_level_count",
            "indexed_count",
        },
        "authorization.bound_v8",
    )
    if (
        bound_v8["directory_name"] != V8_DIRECTORY_NAME
        or exact_sha(bound_v8["prepared_receipt_sha256"], "authorization.bound_v8.prepared_receipt_sha256") != V8_PREPARED_RECEIPT_SHA256
        or exact_sha(bound_v8["bundle_manifest_sha256"], "authorization.bound_v8.bundle_manifest_sha256") != V8_BUNDLE_MANIFEST_SHA256
        or exact_sha(bound_v8["sha256_index_sha256"], "authorization.bound_v8.sha256_index_sha256") != V8_SHA256_INDEX_SHA256
        or exact_int(bound_v8["top_level_count"], "authorization.bound_v8.top_level_count") != V8_TOP_LEVEL_COUNT
        or exact_int(bound_v8["indexed_count"], "authorization.bound_v8.indexed_count") != V8_INDEXED_COUNT
    ):
        raise SmokeError("authorization exact source-v8 binding mismatch")

    if top["exact_isolation_flags"] != ["-I", "-B", "-S"]:
        raise SmokeError("authorization isolation flags mismatch")
    if top["imports_exact"] != ["numpy", "matplotlib"]:
        raise SmokeError("authorization import set/order mismatch")
    environment = exact_object(
        top["environment_policy"],
        {
            "keys",
            "target",
            "scratch_must_be_precreated_empty",
            "global_no_write_claim",
        },
        "authorization.environment_policy",
    )
    if exact_string_list(environment["keys"], "authorization.environment_policy.keys", unique=True) != list(ENVIRONMENT_KEYS):
        raise SmokeError("authorization environment key set/order mismatch")
    if environment["target"] != "HELD_SCRATCH_DIRFD_PROC_PATH":
        raise SmokeError("authorization environment target mismatch")
    exact_bool(environment["scratch_must_be_precreated_empty"], True, "authorization.environment_policy.scratch_must_be_precreated_empty")
    exact_bool(environment["global_no_write_claim"], False, "authorization.environment_policy.global_no_write_claim")
    if top["capability"] != CAPABILITY or args.execute != CAPABILITY:
        raise SmokeError("authorization capability mismatch")

    template = exact_process_argv(
        top["exact_process_argv_template"],
        "authorization.exact_process_argv_template",
    )
    if template.count(AUTH_SHA_PLACEHOLDER) != 1:
        raise SmokeError("authorization argv template must contain one self-SHA placeholder")
    placeholder_index = template.index(AUTH_SHA_PLACEHOLDER)
    if placeholder_index == 0 or template[placeholder_index - 1] != "--trusted-smoke-authorization-sha256":
        raise SmokeError("authorization self-SHA placeholder is in the wrong argv slot")
    normalized_actual = list(actual_cmdline)
    if placeholder_index >= len(normalized_actual):
        raise SmokeError("actual argv is shorter than authorized template")
    if normalized_actual[placeholder_index] != args.trusted_smoke_authorization_sha256:
        raise SmokeError("actual authorization SHA argv slot mismatch")
    normalized_actual[placeholder_index] = AUTH_SHA_PLACEHOLDER
    if normalized_actual != template:
        raise SmokeError("exact /proc/self/cmdline does not match authorization template")
    if actual_cmdline[:6] != [
        f"/proc/self/fd/{INTERPRETER_FD}",
        "-I",
        "-B",
        "-S",
        "-c",
        HELD_SMOKE_BOOTSTRAP_TEXT,
    ]:
        raise SmokeError("held interpreter/bootstrap argv prefix mismatch")
    smoke_start = 6 + 2 * len(HELD_SMOKE_BOOTSTRAP_ENVELOPE_FLAGS)
    if actual_cmdline[smoke_start:] != list(smoke_argv):
        raise SmokeError("/proc/self/cmdline held smoke argv differs from entry argv")
    if paths["smoke_script"] != bootstrap_context["original_smoke_evidence_path"]:
        raise SmokeError("authorized smoke path is not the original evidence path")
    return {
        "paths": paths,
        "root_identity": root_identity,
        "scratch_identity": scratch_identity,
        "expected": expected,
        "decision_id": top["decision_id"],
        "held_byte_bootstrap": held_byte_bootstrap,
    }


def _validate_external_exclusion_entries(value: Any, label: str) -> list[dict[str, Any]]:
    if type(value) is not list:
        raise SmokeError(f"{label}: list required")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if type(item) is not dict or not item:
            raise SmokeError(f"{label}[{index}]: nonempty object required")
        for key, member in item.items():
            exact_string(key, f"{label}[{index}].key")
            if type(member) is str:
                exact_string(member, f"{label}[{index}].{key}", nonempty=False)
            elif type(member) is int:
                exact_int(member, f"{label}[{index}].{key}")
            elif type(member) is bool:
                pass
            elif type(member) is list:
                exact_string_list(member, f"{label}[{index}].{key}")
            else:
                raise SmokeError(f"{label}[{index}].{key}: unsupported exact type")
        result.append(item)
    return result


def validate_build_receipt(
    receipt: dict[str, Any], args: argparse.Namespace, auth: Mapping[str, Any]
) -> dict[str, Any]:
    top = exact_object(
        receipt,
        set(BUILD_RECEIPT_TOP_KEYS),
        "build_receipt",
    )
    if top["schema"] != BUILD_RECEIPT_SCHEMA or top["status"] != BUILD_RECEIPT_STATUS:
        raise SmokeError("build receipt schema/status mismatch")
    if not UTC_RE.fullmatch(exact_string(top["created_utc"], "build_receipt.created_utc")):
        raise SmokeError("build receipt created_utc is not canonical UTC")
    exact_string(top["decision_id"], "build_receipt.decision_id")
    if top["decision_id"] != auth["decision_id"]:
        raise SmokeError("build receipt decision_id differs from smoke authorization")

    authorization = exact_object(
        top["authorization"],
        set(BUILD_RECEIPT_NESTED_KEYS["authorization"]),
        "build_receipt.authorization",
    )
    canonical_absolute_path(authorization["path"], "build_receipt.authorization.path")
    if exact_sha(authorization["sha256"], "build_receipt.authorization.sha256") != args.expected_build_authorization_sha256:
        raise SmokeError("build receipt authorization SHA mismatch")
    exact_string_list(
        authorization["logical_builder_argv"],
        "build_receipt.authorization.logical_builder_argv",
    )

    journal = exact_object(
        top["journal"],
        set(BUILD_RECEIPT_NESTED_KEYS["journal"]),
        "build_receipt.journal",
    )
    for name in (
        "directory",
        "parent_path",
        "begin_path",
        "commit_intent_path",
        "terminal_path",
        "lock_path",
    ):
        canonical_absolute_path(journal[name], f"build_receipt.journal.{name}")
    expected_parent_path = os.fspath(PurePosixPath(args.final_root).parent)
    expected_journal_path = os.fspath(
        PurePosixPath(expected_parent_path)
        / f".result-free-transport-v10.{top['decision_id']}"
    )
    expected_journal_members = {
        "parent_path": expected_parent_path,
        "directory": expected_journal_path,
        "begin_path": os.fspath(PurePosixPath(expected_journal_path) / "BEGIN.json"),
        "commit_intent_path": os.fspath(
            PurePosixPath(expected_journal_path) / "COMMIT_INTENT.json"
        ),
        "terminal_path": args.build_pass_receipt,
        "lock_path": os.fspath(PurePosixPath(expected_journal_path) / "LOCK"),
    }
    for name, expected_path in expected_journal_members.items():
        if journal[name] != expected_path:
            raise SmokeError(f"build receipt journal path relationship mismatch: {name}")
    for name in (
        "directory_device",
        "directory_inode",
        "parent_device",
        "parent_inode",
        "lock_device",
        "lock_inode",
    ):
        exact_int(journal[name], f"build_receipt.journal.{name}")
    if exact_string(journal["lock_method"], "build_receipt.journal.lock_method") != LOCK_METHOD:
        raise SmokeError("build receipt journal lock method mismatch")
    if exact_string(
        journal["terminal_publication_method"],
        "build_receipt.journal.terminal_publication_method",
    ) != TERMINAL_PUBLICATION_METHOD:
        raise SmokeError("build receipt terminal publication method mismatch")
    if exact_string(
        journal["terminal_canonical_visibility_rule"],
        "build_receipt.journal.terminal_canonical_visibility_rule",
    ) != TERMINAL_CANONICAL_VISIBILITY_RULE:
        raise SmokeError("build receipt terminal canonical visibility rule mismatch")
    exact_sha(journal["begin_sha256"], "build_receipt.journal.begin_sha256")
    if exact_sha(journal["commit_intent_sha256"], "build_receipt.journal.commit_intent_sha256") != args.expected_build_commit_intent_sha256:
        raise SmokeError("build receipt commit-intent SHA mismatch")

    publication = exact_object(
        top["publication"],
        set(BUILD_RECEIPT_NESTED_KEYS["publication"]),
        "build_receipt.publication",
    )
    if publication["method"] != "renameat2(RENAME_NOREPLACE)_DIRFD_RELATIVE":
        raise SmokeError("build receipt publication method mismatch")
    if canonical_absolute_path(publication["final_root_path"], "build_receipt.publication.final_root_path") != args.final_root:
        raise SmokeError("build receipt final ROOT path mismatch")
    for name in ("final_root_device", "final_root_inode", "staging_device", "staging_inode"):
        exact_int(publication[name], f"build_receipt.publication.{name}")
    exact_bool(publication["final_inode_equals_staging"], True, "build_receipt.publication.final_inode_equals_staging")
    if publication["final_root_device"] != args.expected_final_root_device or publication["final_root_inode"] != args.expected_final_root_inode:
        raise SmokeError("build receipt final ROOT identity mismatch")
    if publication["staging_device"] != publication["final_root_device"] or publication["staging_inode"] != publication["final_root_inode"]:
        raise SmokeError("build receipt staging/final inode continuity mismatch")
    if exact_sha(
        publication["files_only_full_root_digest"],
        "build_receipt.publication.files_only_full_root_digest",
    ) != args.expected_files_only_full_root_digest:
        raise SmokeError("build receipt files-only full ROOT digest mismatch")
    if exact_sha(
        publication["structural_full_root_digest"],
        "build_receipt.publication.structural_full_root_digest",
    ) != args.expected_structural_full_root_digest:
        raise SmokeError("build receipt structural full ROOT digest mismatch")

    runtime = exact_object(
        top["runtime"],
        set(BUILD_RECEIPT_NESTED_KEYS["runtime"]),
        "build_receipt.runtime",
    )
    expected_manifest_path = os.fspath(PurePosixPath(args.final_root) / "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json")
    expected_private_path = os.fspath(PurePosixPath(args.final_root) / "private_runtime_site_packages")
    expected_bundle_path = os.fspath(PurePosixPath(args.final_root) / "bundle")
    if canonical_absolute_path(runtime["manifest_path"], "build_receipt.runtime.manifest_path") != expected_manifest_path:
        raise SmokeError("build receipt runtime manifest path mismatch")
    if canonical_absolute_path(runtime["private_root_path"], "build_receipt.runtime.private_root_path") != expected_private_path:
        raise SmokeError("build receipt private root path mismatch")
    if canonical_absolute_path(runtime["bundle_root_path"], "build_receipt.runtime.bundle_root_path") != expected_bundle_path:
        raise SmokeError("build receipt bundle root path mismatch")
    for name in (
        "private_root_device",
        "private_root_inode",
        "bundle_root_device",
        "bundle_root_inode",
    ):
        exact_int(runtime[name], f"build_receipt.runtime.{name}")
    runtime_expected = {
        "manifest_sha256": args.expected_runtime_manifest_sha256,
        "files_only_runtime_root_digest": args.expected_files_only_runtime_root_digest,
        "files_only_private_root_digest": args.expected_files_only_private_root_digest,
        "structural_private_tree_digest": args.expected_structural_private_tree_digest,
    }
    for name, expected in runtime_expected.items():
        if exact_sha(runtime[name], f"build_receipt.runtime.{name}") != expected:
            raise SmokeError(f"build receipt runtime {name} mismatch")
    if runtime["files_only_runtime_root_digest"] != runtime["files_only_private_root_digest"]:
        raise SmokeError("build receipt runtime/private files-only digests differ")

    support_files = exact_object(
        top["support_files"], set(SUPPORT_SHA256), "build_receipt.support_files"
    )
    validated_support: dict[str, dict[str, Any]] = {}
    for name in sorted(SUPPORT_SHA256):
        item = exact_object(
            support_files[name],
            set(SUPPORT_FILE_RECEIPT_KEYS),
            f"build_receipt.support_files.{name}",
        )
        expected_path = os.fspath(PurePosixPath(args.final_root) / name)
        if canonical_absolute_path(
            item["path"], f"build_receipt.support_files.{name}.path"
        ) != expected_path:
            raise SmokeError(f"build receipt support path mismatch: {name}")
        digest = exact_sha(item["sha256"], f"build_receipt.support_files.{name}.sha256")
        if digest != SUPPORT_SHA256[name]:
            raise SmokeError(f"build receipt support pinned SHA mismatch: {name}")
        validated_support[name] = {
            "path": expected_path,
            "device": exact_int(item["device"], f"build_receipt.support_files.{name}.device"),
            "inode": exact_int(item["inode"], f"build_receipt.support_files.{name}.inode"),
            "sha256": digest,
            "size_bytes": exact_int(
                item["size_bytes"], f"build_receipt.support_files.{name}.size_bytes"
            ),
        }

    bound_v8 = exact_object(
        top["bound_v8"],
        {
            "bundle_path",
            "prepared_receipt_sha256",
            "bundle_manifest_sha256",
            "sha256_index_sha256",
            "top_level_count",
            "indexed_count",
        },
        "build_receipt.bound_v8",
    )
    # The core PASS receipt records the authenticated *source* V8 bundle path;
    # the published copy is independently opened below as ROOT/bundle and its
    # complete exact-set SHA index is reverified.  Do not reinterpret this
    # source-evidence field as the destination path.
    source_bundle_path = canonical_absolute_path(
        bound_v8["bundle_path"], "build_receipt.bound_v8.bundle_path"
    )
    if PurePosixPath(source_bundle_path).name != V8_DIRECTORY_NAME:
        raise SmokeError("build receipt source V8 bundle directory name mismatch")
    if (
        exact_sha(bound_v8["prepared_receipt_sha256"], "build_receipt.bound_v8.prepared_receipt_sha256") != V8_PREPARED_RECEIPT_SHA256
        or exact_sha(bound_v8["bundle_manifest_sha256"], "build_receipt.bound_v8.bundle_manifest_sha256") != V8_BUNDLE_MANIFEST_SHA256
        or exact_sha(bound_v8["sha256_index_sha256"], "build_receipt.bound_v8.sha256_index_sha256") != V8_SHA256_INDEX_SHA256
        or exact_int(bound_v8["top_level_count"], "build_receipt.bound_v8.top_level_count") != V8_TOP_LEVEL_COUNT
        or exact_int(bound_v8["indexed_count"], "build_receipt.bound_v8.indexed_count") != V8_INDEXED_COUNT
    ):
        raise SmokeError("build receipt exact source-v8 binding mismatch")

    source = exact_object(
        top["source_runtime"],
        {
            "python_path",
            "python_sha256",
            "site_packages_path",
            "site_packages_device",
            "site_packages_inode",
            "source_inventory_digest",
        },
        "build_receipt.source_runtime",
    )
    if canonical_absolute_path(source["python_path"], "build_receipt.source_runtime.python_path") != auth["paths"]["source_python"]:
        raise SmokeError("build receipt source Python path mismatch")
    if exact_sha(source["python_sha256"], "build_receipt.source_runtime.python_sha256") != args.expected_python_sha256:
        raise SmokeError("build receipt source Python SHA mismatch")
    canonical_absolute_path(source["site_packages_path"], "build_receipt.source_runtime.site_packages_path")
    exact_int(source["site_packages_device"], "build_receipt.source_runtime.site_packages_device")
    exact_int(source["site_packages_inode"], "build_receipt.source_runtime.site_packages_inode")
    exact_sha(source["source_inventory_digest"], "build_receipt.source_runtime.source_inventory_digest")

    exclusions = exact_object(
        top["external_record_exclusions"],
        {"policy", "count", "entries", "evidence_digest"},
        "build_receipt.external_record_exclusions",
    )
    if exact_string(
        exclusions["policy"], "build_receipt.external_record_exclusions.policy"
    ) != "EXACT7_ALLOWLIST_EXCLUDED_FROM_PRIVATE_TREE":
        raise SmokeError("build receipt exclusion policy mismatch")
    entries = _validate_external_exclusion_entries(exclusions["entries"], "build_receipt.external_record_exclusions.entries")
    if entries != EXTERNAL_RECORD_EXCLUSION_ENTRIES:
        raise SmokeError("build receipt exact7 exclusion entries mismatch")
    if exact_int(exclusions["count"], "build_receipt.external_record_exclusions.count") != 7:
        raise SmokeError("build receipt exclusion count mismatch")
    expected_exclusion_digest = hashlib.sha256(
        (
            json.dumps(entries, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
    ).hexdigest()
    if exact_sha(
        exclusions["evidence_digest"],
        "build_receipt.external_record_exclusions.evidence_digest",
    ) != expected_exclusion_digest:
        raise SmokeError("build receipt exact7 exclusion evidence digest mismatch")

    package = exact_object(
        top["package_binding"],
        set(BUILD_RECEIPT_NESTED_KEYS["package_binding"]),
        "build_receipt.package_binding",
    )
    for name, value in package.items():
        if name != "v10_smoke_bootstrap_size_bytes":
            exact_sha(value, f"build_receipt.package_binding.{name}")
    if package["v10_smoke_sha256"] != args.expected_smoke_script_sha256:
        raise SmokeError("build receipt v10 smoke SHA mismatch")
    for stem, expected_digest in V9_NEGATIVE_QA_SHA256.items():
        if package[f"v9_negative_qa_{stem}_sha256"] != expected_digest:
            raise SmokeError("build receipt v9 negative QA binding mismatch")
    for stem, expected_digest in V8_NEGATIVE_QA_SHA256.items():
        if package[f"v8_negative_qa_{stem}_sha256"] != expected_digest:
            raise SmokeError(
                f"build receipt v8 negative QA {stem} SHA mismatch"
            )
    for stem, expected_digest in V7_NEGATIVE_QA_SHA256.items():
        if package[f"v7_negative_qa_{stem}_sha256"] != expected_digest:
            raise SmokeError(
                f"build receipt v7 negative QA {stem} SHA mismatch"
            )
    if (
        package["v10_smoke_bootstrap_sha256"]
        != HELD_SMOKE_BOOTSTRAP_SHA256
        or exact_int(
            package["v10_smoke_bootstrap_size_bytes"],
            "build_receipt.package_binding.v10_smoke_bootstrap_size_bytes",
        ) != len(HELD_SMOKE_BOOTSTRAP_TEXT.encode("utf-8"))
    ):
        raise SmokeError("build receipt v10 held bootstrap binding mismatch")

    trusted_launch = exact_object(
        top["trusted_launch"],
        set(BUILD_RECEIPT_NESTED_KEYS["trusted_launch"]),
        "build_receipt.trusted_launch",
    )
    if (
        trusted_launch["schema"] != HELD_BUILDER_LAUNCH_SCHEMA
        or trusted_launch["status"] != HELD_BUILDER_LAUNCH_STATUS
        or trusted_launch["method"] != HELD_BUILDER_LAUNCH_METHOD
    ):
        raise SmokeError("build receipt trusted-launch schema/status/method mismatch")
    if (
        exact_int(trusted_launch["interpreter_fd"], "build_receipt.trusted_launch.interpreter_fd")
        != INTERPRETER_FD
        or exact_int(trusted_launch["builder_source_fd"], "build_receipt.trusted_launch.builder_source_fd")
        != SMOKE_SOURCE_FD
        or trusted_launch["interpreter_proc_path"] != f"/proc/self/fd/{INTERPRETER_FD}"
        or trusted_launch["builder_source_proc_path"] != f"/proc/self/fd/{SMOKE_SOURCE_FD}"
    ):
        raise SmokeError("build receipt trusted-launch held FD/path mismatch")
    exact_bool(
        trusted_launch["interpreter_fd_inheritable"], True,
        "build_receipt.trusted_launch.interpreter_fd_inheritable",
    )
    exact_bool(
        trusted_launch["builder_source_fd_inheritable"], False,
        "build_receipt.trusted_launch.builder_source_fd_inheritable",
    )
    launch_identity_keys = {
        "device", "inode", "size_bytes", "mtime_ns", "mode", "nlink"
    }
    for name in ("interpreter_identity", "builder_source_identity"):
        identity = exact_object(
            trusted_launch[name], launch_identity_keys,
            f"build_receipt.trusted_launch.{name}",
        )
        for member in ("device", "inode", "size_bytes", "mtime_ns", "nlink"):
            exact_int(identity[member], f"build_receipt.trusted_launch.{name}.{member}")
        if re.fullmatch(r"0[0-7]{3}", exact_string(
            identity["mode"], f"build_receipt.trusted_launch.{name}.mode"
        )) is None:
            raise SmokeError("build receipt trusted-launch identity mode mismatch")
    if trusted_launch["builder_source_identity"]["mode"] != "0444" or (
        trusted_launch["builder_source_identity"]["nlink"] != 1
        or trusted_launch["interpreter_identity"]["nlink"] != 1
    ):
        raise SmokeError("build receipt trusted-launch identity mode/nlink mismatch")
    if exact_sha(
        trusted_launch["interpreter_sha256"],
        "build_receipt.trusted_launch.interpreter_sha256",
    ) != args.expected_python_sha256:
        raise SmokeError("build receipt trusted-launch interpreter SHA mismatch")
    if exact_sha(
        trusted_launch["builder_source_sha256"],
        "build_receipt.trusted_launch.builder_source_sha256",
    ) != package["v10_builder_sha256"]:
        raise SmokeError("build receipt trusted-launch builder SHA mismatch")
    canonical_absolute_path(
        trusted_launch["builder_original_evidence_path"],
        "build_receipt.trusted_launch.builder_original_evidence_path",
    )
    canonical_absolute_path(
        trusted_launch["outer_launch_receipt_path"],
        "build_receipt.trusted_launch.outer_launch_receipt_path",
    )
    exact_sha(
        trusted_launch["outer_launch_receipt_sha256"],
        "build_receipt.trusted_launch.outer_launch_receipt_sha256",
    )
    outer_argv = exact_string_list(
        trusted_launch["outer_process_argv"],
        "build_receipt.trusted_launch.outer_process_argv",
    )
    outer_argv_sha = hashlib.sha256(
        (json.dumps(outer_argv, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    ).hexdigest()
    if exact_sha(
        trusted_launch["outer_process_argv_sha256"],
        "build_receipt.trusted_launch.outer_process_argv_sha256",
    ) != outer_argv_sha:
        raise SmokeError("build receipt trusted-launch outer argv digest mismatch")

    scope = exact_object(
        top["scope"],
        {
            "result_free_transport_runtime_layout_only",
            "result_accessed",
            "signals_sent",
            "processes_inspected",
            "controller_or_outer_main_executed",
            "deployment_or_resume_executed",
            "smoke_executed",
            "linux_integration",
        },
        "build_receipt.scope",
    )
    exact_bool(scope["result_free_transport_runtime_layout_only"], True, "build_receipt.scope.result_free_transport_runtime_layout_only")
    if exact_string(
        scope["linux_integration"], "build_receipt.scope.linux_integration"
    ) != "PASS_LINUX_RENAMEAT2_NOREPLACE":
        raise SmokeError("build PASS receipt lacks real Linux renameat2 integration")
    for name in (
        "result_accessed",
        "signals_sent",
        "processes_inspected",
        "controller_or_outer_main_executed",
        "deployment_or_resume_executed",
        "smoke_executed",
    ):
        exact_bool(scope[name], False, f"build_receipt.scope.{name}")
    return {
        "runtime": runtime,
        "bundle": bound_v8,
        "support_files": validated_support,
        "publication": publication,
        "journal": journal,
        "package": package,
        "trusted_launch": trusted_launch,
        "decision_id": top["decision_id"],
    }


def parse_sha_index(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SmokeError("v10 SHA index is not strict UTF-8") from exc
    records: dict[str, str] = {}
    order: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA_RE.fullmatch(parts[0]):
            raise SmokeError(f"v10 SHA index line {number} invalid")
        name = parts[1]
        if Path(name).name != name or name in records or any(ord(character) < 0x20 for character in name):
            raise SmokeError(f"v10 SHA index member {number} unsafe/duplicate")
        records[name] = parts[0]
        order.append(name)
    if order != sorted(order):
        raise SmokeError("v10 SHA index is not canonically sorted")
    return records


def verify_v10_bundle(bundle_fd: int) -> dict[str, Any]:
    names = fresh_directory_names(bundle_fd, "v10 bundle:before")
    if len(names) != V8_TOP_LEVEL_COUNT:
        raise SmokeError("v10 bundle top-level count/uniqueness mismatch")
    index_fd = open_child(bundle_fd, "SHA256SUMS", "v10 SHA index", directory=False)
    try:
        index_raw = read_fd_bytes(index_fd, "v10 SHA index", maximum_size=4 * 1024 * 1024)
        index_sha = hashlib.sha256(index_raw).hexdigest()
    finally:
        os.close(index_fd)
    if index_sha != V8_SHA256_INDEX_SHA256:
        raise SmokeError("v10 SHA index pinned SHA mismatch")
    records = parse_sha_index(index_raw)
    if len(records) != V8_INDEXED_COUNT or set(names) != set(records) | {"SHA256SUMS"}:
        raise SmokeError("v10 exact indexed/top-level set mismatch")
    actual: dict[str, str] = {}
    for name, expected in records.items():
        fd = open_child(bundle_fd, name, f"v10 bundle:{name}", directory=False)
        try:
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != 0o444:
                raise SmokeError(f"v10 bundle file mode mismatch: {name}")
            digest, _ = digest_fd(fd, f"v10 bundle:{name}")
        finally:
            os.close(fd)
        if digest != expected:
            raise SmokeError(f"v10 indexed SHA mismatch: {name}")
        actual[name] = digest
    if actual.get("PREPARED_RESULT_FREE_RECEIPT.json") != V8_PREPARED_RECEIPT_SHA256:
        raise SmokeError("v10 prepared receipt pinned SHA mismatch")
    if actual.get("BUNDLE_MANIFEST.json") != V8_BUNDLE_MANIFEST_SHA256:
        raise SmokeError("v10 bundle manifest pinned SHA mismatch")
    if names != fresh_directory_names(bundle_fd, "v10 bundle:after"):
        raise SmokeError("v10 bundle entries changed during verification")
    return {
        "indexed_count": len(records),
        "top_level_count": len(names),
        "prepared_receipt_sha256": actual["PREPARED_RESULT_FREE_RECEIPT.json"],
        "bundle_manifest_sha256": actual["BUNDLE_MANIFEST.json"],
        "sha256_index_sha256": index_sha,
        "member_sha256": actual,
    }


def validate_runtime_manifest(
    manifest: dict[str, Any],
    *,
    expected_private_path: str,
    actual_records: list[dict[str, Any]],
    expected_root_digest: str,
) -> dict[str, Any]:
    top = exact_object(
        manifest,
        {
            "schema",
            "status",
            "site_packages_root",
            "exact_file_set",
            "files",
            "distributions",
            "files_only_digest_algorithm",
            "files_only_root_digest",
        },
        "runtime_manifest",
    )
    if top["schema"] != RUNTIME_MANIFEST_SCHEMA or top["status"] != RUNTIME_MANIFEST_STATUS:
        raise SmokeError("runtime manifest schema/status mismatch")
    if canonical_absolute_path(top["site_packages_root"], "runtime_manifest.site_packages_root") != expected_private_path:
        raise SmokeError("runtime manifest private root path mismatch")
    exact_bool(top["exact_file_set"], True, "runtime_manifest.exact_file_set")
    if type(top["files"]) is not list or not top["files"]:
        raise SmokeError("runtime manifest files must be a nonempty list")
    listed: list[dict[str, Any]] = []
    previous = ""
    for index, value in enumerate(top["files"]):
        item = exact_object(value, {"relative_path", "sha256", "size_bytes", "mode"}, f"runtime_manifest.files[{index}]")
        relative = safe_relative_path(item["relative_path"], f"runtime_manifest.files[{index}].relative_path")
        if relative <= previous:
            raise SmokeError("runtime manifest files are not strictly sorted/unique")
        previous = relative
        listed.append(
            {
                "relative_path": relative,
                "sha256": exact_sha(item["sha256"], f"runtime_manifest.files[{index}].sha256"),
                "size_bytes": exact_int(item["size_bytes"], f"runtime_manifest.files[{index}].size_bytes"),
                "mode": exact_string(item["mode"], f"runtime_manifest.files[{index}].mode"),
            }
        )
        if listed[-1]["mode"] != "0444":
            raise SmokeError("runtime manifest file mode must be 0444")
    if listed != actual_records:
        raise SmokeError("runtime manifest exact file records differ from held private tree")
    if exact_string(
        top["files_only_digest_algorithm"],
        "runtime_manifest.files_only_digest_algorithm",
    ) != FILES_ONLY_DIGEST_ALGORITHM:
        raise SmokeError("runtime manifest files-only digest algorithm mismatch")
    calculated = files_only_digest(actual_records)
    if exact_sha(
        top["files_only_root_digest"], "runtime_manifest.files_only_root_digest"
    ) != calculated or calculated != expected_root_digest:
        raise SmokeError("runtime manifest root digest mismatch")
    distributions = exact_object(top["distributions"], {"numpy", "matplotlib"}, "runtime_manifest.distributions")
    normalized: dict[str, dict[str, str]] = {}
    actual_map = {item["relative_path"]: item for item in actual_records}
    for name in ("numpy", "matplotlib"):
        item = exact_object(
            distributions[name],
            {"distribution_record_relative_path", "import_relative_path", "version"},
            f"runtime_manifest.distributions.{name}",
        )
        record_path = safe_relative_path(item["distribution_record_relative_path"], f"runtime_manifest.distributions.{name}.distribution_record_relative_path")
        import_path = safe_relative_path(item["import_relative_path"], f"runtime_manifest.distributions.{name}.import_relative_path")
        version = exact_string(item["version"], f"runtime_manifest.distributions.{name}.version")
        if record_path not in actual_map or import_path not in actual_map:
            raise SmokeError(f"runtime manifest distribution path absent: {name}")
        normalized[name] = {
            "distribution_record_relative_path": record_path,
            "import_relative_path": import_path,
            "version": version,
        }
    return {
        "files": actual_map,
        "distributions": normalized,
        "files_only_root_digest": calculated,
    }


def _stdlib_roots_before_private_insert() -> tuple[list[str], list[Path]]:
    entries = list(sys.path)
    roots: list[Path] = []
    for index, entry in enumerate(entries):
        if type(entry) is not str or not entry or not os.path.isabs(entry):
            raise SmokeError(f"isolated baseline sys.path[{index}] is not a nonempty absolute path")
        parts = {part.lower() for part in PurePosixPath(entry).parts}
        if "site-packages" in parts or "dist-packages" in parts:
            raise SmokeError("isolated baseline sys.path contains a package site")
        path = Path(entry)
        if path.is_dir():
            roots.append(path.resolve())
    for prefix in {sys.base_prefix, sys.base_exec_prefix}:
        if prefix:
            roots.append(Path(prefix).resolve())
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return entries, unique


def _path_is_below(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or resolved.is_relative_to(root) for root in roots)


class RecursiveInotifyGuard:
    """Monitor configured directory and held-regular-inode failure masks.

    Directory watches cover the explicit ``INOTIFY_DIRECTORY_WATCH_MASK``.
    Every regular inode also receives the explicit
    ``INOTIFY_REGULAR_FILE_WATCH_MASK`` through its already-held
    ``/proc/self/fd`` handle, so a write through a hard link created outside
    ROOT is covered even when no watched directory entry changes.  Setup is
    bracketed by entry/digest checks and the caller repeats complete
    inventories after imports; this is a layered, mask-scoped guard, not a
    claim that Linux inotify reports every conceivable filesystem mutation.
    """

    def __init__(self, root_fd: int) -> None:
        if not sys.platform.startswith("linux"):
            raise SmokeError("recursive inotify mutation guard requires Linux")
        libc = ctypes.CDLL(None, use_errno=True)
        init = getattr(libc, "inotify_init1", None)
        add = getattr(libc, "inotify_add_watch", None)
        if init is None or add is None:
            raise SmokeError("libc inotify_init1/inotify_add_watch unavailable")
        init.argtypes = [ctypes.c_int]
        init.restype = ctypes.c_int
        add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        add.restype = ctypes.c_int
        fd = init(O_CLOEXEC | O_NONBLOCK)
        if fd < 0:
            raise SmokeError(f"inotify_init1 failed errno={ctypes.get_errno()}")
        self.fd = fd
        self._add = add
        self.watch_table: dict[int, dict[str, Any]] = {}
        try:
            self._watch_tree(root_fd, ".")
            self.assert_clean("recursive watch setup")
        except BaseException:
            self.close()
            raise

    def _add_directory(self, directory_fd: int, relative: str) -> None:
        # The proc-fd symlink is a trusted reference to the already-held inode;
        # appending '/.' makes the final watched component a directory so
        # IN_DONT_FOLLOW does not reject the proc-fd symlink itself.
        proc_path = f"/proc/self/fd/{directory_fd}/.".encode("ascii")
        info = os.fstat(directory_fd)
        if not stat.S_ISDIR(info.st_mode):
            raise SmokeError(f"inotify directory watch target is not a directory: {relative}")
        wd = self._add(self.fd, proc_path, INOTIFY_DIRECTORY_WATCH_MASK)
        if wd < 0:
            raise SmokeError(
                f"inotify_add_watch failed for {relative}: errno={ctypes.get_errno()}"
            )
        entry = {
            "kind": "directory",
            "relative_path": relative,
            "device": info.st_dev,
            "inode": info.st_ino,
        }
        prior = self.watch_table.get(wd)
        if prior is not None and prior != entry:
            raise SmokeError("inotify watch descriptor unexpectedly reused during setup")
        self.watch_table[wd] = entry

    def _add_regular_file(self, file_fd: int, relative: str) -> None:
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise SmokeError(f"inotify file watch target is not regular: {relative}")
        # Do not use IN_DONT_FOLLOW here: /proc/self/fd/<n> is intentionally a
        # trusted kernel link to the already-held regular inode, which is the
        # object that must be watched rather than the procfs symlink itself.
        proc_path = f"/proc/self/fd/{file_fd}".encode("ascii")
        wd = self._add(self.fd, proc_path, INOTIFY_REGULAR_FILE_WATCH_MASK)
        if wd < 0:
            raise SmokeError(
                f"inotify_add_watch failed for regular {relative}: "
                f"errno={ctypes.get_errno()}"
            )
        after = os.fstat(file_fd)
        if _stable_stat_tuple(info) != _stable_stat_tuple(after):
            raise SmokeError(
                f"regular inode changed while adding inotify watch: {relative}"
            )
        entry = {
            "kind": "regular",
            "relative_path": relative,
            "device": info.st_dev,
            "inode": info.st_ino,
        }
        prior = self.watch_table.get(wd)
        if prior is not None and prior != entry:
            raise SmokeError("inotify watch descriptor unexpectedly reused during setup")
        self.watch_table[wd] = entry

    def _watch_tree(self, directory_fd: int, relative: str) -> None:
        self._add_directory(directory_fd, relative)
        names_before = fresh_directory_names(
            directory_fd, f"inotify setup:{relative}:before"
        )
        cursor = fresh_directory_cursor(directory_fd, f"inotify setup:{relative}:walk")
        try:
            for name in names_before:
                try:
                    child = os.open(
                        name,
                        os.O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC,
                        dir_fd=cursor,
                    )
                except OSError as exc:
                    raise SmokeError(
                        f"inotify setup nofollow open failed at {relative}/{name}: {exc}"
                    ) from exc
                try:
                    child_info = os.fstat(child)
                    child_relative = name if relative == "." else f"{relative}/{name}"
                    if stat.S_ISDIR(child_info.st_mode):
                        self._watch_tree(child, child_relative)
                    elif stat.S_ISREG(child_info.st_mode):
                        if child_info.st_nlink != 1:
                            raise SmokeError(
                                f"inotify setup regular inode is not single-link: "
                                f"{child_relative}"
                            )
                        digest_before, size_before = digest_fd(
                            child, f"inotify setup pre-watch:{child_relative}"
                        )
                        self._add_regular_file(child, child_relative)
                        digest_after, size_after = digest_fd(
                            child, f"inotify setup post-watch:{child_relative}"
                        )
                        if (
                            digest_after != digest_before
                            or size_after != size_before
                            or os.fstat(child).st_nlink != 1
                        ):
                            raise SmokeError(
                                f"regular inode changed while its watch was installed: "
                                f"{child_relative}"
                            )
                    else:
                        raise SmokeError(
                            f"inotify setup encountered symlink/special member: "
                            f"{child_relative}"
                        )
                finally:
                    os.close(child)
        finally:
            os.close(cursor)
        if names_before != fresh_directory_names(
            directory_fd, f"inotify setup:{relative}:after"
        ):
            raise SmokeError("directory entries changed during recursive watch setup")

    def events(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        while True:
            try:
                raw = os.read(self.fd, 1024 * 1024)
            except BlockingIOError:
                break
            if not raw:
                break
            offset = 0
            while offset < len(raw):
                if len(raw) - offset < INOTIFY_EVENT_HEADER.size:
                    raise SmokeError("truncated inotify event header")
                wd, mask, cookie, length = INOTIFY_EVENT_HEADER.unpack_from(raw, offset)
                offset += INOTIFY_EVENT_HEADER.size
                if length > len(raw) - offset:
                    raise SmokeError("truncated inotify event name")
                name_raw = raw[offset : offset + length]
                offset += length
                name = name_raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
                result.append(
                    {
                        "watch": self.watch_table.get(
                            wd,
                            {
                                "kind": "unknown",
                                "relative_path": "<unknown>",
                                "device": -1,
                                "inode": -1,
                            },
                        ),
                        "mask": mask,
                        "cookie": cookie,
                        "name": name,
                    }
                )
        return result

    def assert_clean(self, phase: str) -> None:
        bad = [event for event in self.events() if event["mask"] & INOTIFY_FAILURE_MASK]
        if bad:
            raise SmokeError(
                f"concurrent ROOT mutation detected by inotify during {phase}: {bad[:8]}"
            )

    def close(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
            self.fd = -1

    @property
    def watch_paths(self) -> dict[int, str]:
        """Compatibility-free display projection; evidence lives in watch_table."""
        return {
            wd: entry["relative_path"]
            for wd, entry in self.watch_table.items()
        }


def _looks_like_elf_dso(relative: str) -> bool:
    return re.search(r"\.so(?:\.[A-Za-z0-9_+.-]+)?$", PurePosixPath(relative).name) is not None


def _read_stable_regular_bytes(
    fd: int, label: str, *, maximum_size: int
) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise SmokeError(f"{label}: ELF source is not a regular file")
    if before.st_size <= 0 or before.st_size > maximum_size:
        raise SmokeError(f"{label}: ELF size outside allowed bound")
    chunks: list[bytes] = []
    offset = 0
    while offset < before.st_size:
        block = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
        if not block:
            raise SmokeError(f"{label}: premature EOF")
        chunks.append(block)
        offset += len(block)
    if os.pread(fd, 1, offset):
        raise SmokeError(f"{label}: ELF grew during read")
    after = os.fstat(fd)
    if _stable_stat_tuple(before) != _stable_stat_tuple(after):
        raise SmokeError(f"{label}: ELF identity changed during read")
    return b"".join(chunks)


def parse_elf64_dynamic_bytes(data: bytes, label: str) -> dict[str, Any]:
    """Parse the bounded ELF64 little-endian dynamic string contract."""
    if (
        len(data) < 64
        or data[:4] != ELF_MAGIC
        or data[4] != ELFCLASS64
        or data[5] != ELFDATA2LSB
    ):
        raise SmokeError(f"{label}: ELF64 little-endian object required")
    try:
        header = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
    except struct.error as exc:
        raise SmokeError(f"{label}: truncated ELF header") from exc
    machine = header[1]
    phoff = header[4]
    phentsize = header[8]
    phnum = header[9]
    if phentsize < 56 or phnum <= 0 or phnum > 65535:
        raise SmokeError(f"{label}: invalid ELF program-header table")
    if phoff > len(data) or phnum * phentsize > len(data) - phoff:
        raise SmokeError(f"{label}: ELF program-header table out of bounds")
    loads: list[tuple[int, int, int]] = []
    dynamic: tuple[int, int] | None = None
    for index in range(phnum):
        offset = phoff + index * phentsize
        try:
            values = struct.unpack_from("<IIQQQQQQ", data, offset)
        except struct.error as exc:
            raise SmokeError(f"{label}: truncated ELF program header") from exc
        p_type, _flags, p_offset, p_vaddr, _p_paddr, p_filesz, _p_memsz, _align = values
        if p_offset > len(data) or p_filesz > len(data) - p_offset:
            raise SmokeError(f"{label}: ELF segment out of file bounds")
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            if dynamic is not None:
                raise SmokeError(f"{label}: multiple PT_DYNAMIC segments")
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        raise SmokeError(f"{label}: shared object has no PT_DYNAMIC")
    dynamic_offset, dynamic_size = dynamic
    if dynamic_size % 16 != 0:
        raise SmokeError(f"{label}: misaligned ELF dynamic table")
    string_table_vaddr: int | None = None
    string_table_size: int | None = None
    string_offsets: dict[int, list[int]] = {
        DT_NEEDED: [],
        DT_SONAME: [],
        DT_RPATH: [],
        DT_RUNPATH: [],
    }
    saw_null = False
    for offset in range(dynamic_offset, dynamic_offset + dynamic_size, 16):
        tag, value = struct.unpack_from("<QQ", data, offset)
        if tag == DT_NULL:
            saw_null = True
            break
        if tag == DT_STRTAB:
            if string_table_vaddr is not None:
                raise SmokeError(f"{label}: duplicate DT_STRTAB entry")
            string_table_vaddr = value
        elif tag == DT_STRSZ:
            if string_table_size is not None:
                raise SmokeError(f"{label}: duplicate DT_STRSZ entry")
            string_table_size = value
        elif tag in string_offsets:
            string_offsets[tag].append(value)
    if not saw_null or string_table_vaddr is None or string_table_size is None:
        raise SmokeError(f"{label}: incomplete ELF dynamic string table")
    if string_table_size <= 0:
        raise SmokeError(f"{label}: ELF dynamic string table has invalid size")
    mapped_loads: list[tuple[int, int, int]] = []
    for vaddr, file_offset, file_size in loads:
        if vaddr <= string_table_vaddr and string_table_vaddr - vaddr < file_size:
            mapped_loads.append((vaddr, file_offset, file_size))
    if len(mapped_loads) != 1:
        raise SmokeError(
            f"{label}: ELF dynamic string-table address is not uniquely file-backed"
        )
    load_vaddr, load_file_offset, load_file_size = mapped_loads[0]
    relative_in_load = string_table_vaddr - load_vaddr
    if string_table_size > load_file_size - relative_in_load:
        raise SmokeError(
            f"{label}: ELF dynamic string table crosses its PT_LOAD file-backed range"
        )
    string_table_offset = load_file_offset + relative_in_load
    if (
        string_table_offset > len(data)
        or string_table_size > len(data) - string_table_offset
    ):
        raise SmokeError(f"{label}: ELF dynamic string table out of file bounds")

    def dynamic_string(relative_offset: int) -> str:
        if relative_offset < 0 or relative_offset >= string_table_size:
            raise SmokeError(f"{label}: ELF dynamic string offset out of bounds")
        start = string_table_offset + relative_offset
        stop = data.find(b"\0", start, string_table_offset + string_table_size)
        if stop < 0:
            raise SmokeError(f"{label}: unterminated ELF dynamic string")
        try:
            value = data[start:stop].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SmokeError(f"{label}: non-UTF-8 ELF dynamic string") from exc
        if not value or "\0" in value:
            raise SmokeError(f"{label}: invalid ELF dynamic string")
        return value

    sonames = [dynamic_string(value) for value in string_offsets[DT_SONAME]]
    if len(sonames) > 1:
        raise SmokeError(f"{label}: multiple DT_SONAME entries")
    needed_values = [
        dynamic_string(value) for value in string_offsets[DT_NEEDED]
    ]
    if any("/" in value for value in [*needed_values, *sonames]):
        raise SmokeError(f"{label}: DT_NEEDED/DT_SONAME must be basenames")
    rpath_values = [dynamic_string(value) for value in string_offsets[DT_RPATH]]
    runpath_values = [dynamic_string(value) for value in string_offsets[DT_RUNPATH]]
    if len(rpath_values) > 1 or len(runpath_values) > 1:
        raise SmokeError(f"{label}: duplicate RPATH/RUNPATH entries")
    return {
        "elf_class": "ELF64",
        "byte_order": "little",
        "machine": machine,
        "soname": sonames[0] if sonames else None,
        "needed": needed_values,
        "rpath": rpath_values[0].split(":") if rpath_values else [],
        "runpath": runpath_values[0].split(":") if runpath_values else [],
    }


def parse_elf64_dynamic_fd(fd: int, label: str) -> dict[str, Any]:
    return parse_elf64_dynamic_bytes(
        _read_stable_regular_bytes(fd, label, maximum_size=512 * 1024 * 1024),
        label,
    )


def _decode_proc_maps_path(value: str) -> tuple[str, bool]:
    deleted = value.endswith(" (deleted)")
    if deleted:
        value = value[: -len(" (deleted)")]

    def replace(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 8))

    return re.sub(r"\\([0-7]{3})", replace, value), deleted


def read_proc_self_maps() -> list[dict[str, Any]]:
    if not sys.platform.startswith("linux"):
        raise SmokeError("/proc/self/maps evidence requires Linux")
    fd = os.open("/proc/self/maps", os.O_RDONLY | O_CLOEXEC)
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    try:
        lines = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SmokeError("/proc/self/maps is not UTF-8") from exc
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for line in lines.splitlines():
        match = PROC_MAP_LINE_RE.fullmatch(line)
        if match is None:
            raise SmokeError(f"unparseable /proc/self/maps line: {line!r}")
        start, end, perms, offset, major, minor, inode, raw_path = match.groups()
        inode_value = int(inode)
        if inode_value == 0 or raw_path is None or not raw_path.startswith("/"):
            continue
        path, deleted = _decode_proc_maps_path(raw_path)
        device = os.makedev(int(major, 16), int(minor, 16))
        key = (device, inode_value)
        entry = grouped.setdefault(
            key,
            {
                "device": device,
                "inode": inode_value,
                "paths": set(),
                "deleted": False,
                "segments": [],
            },
        )
        entry["paths"].add(path)
        entry["deleted"] = entry["deleted"] or deleted
        entry["segments"].append(
            {
                "start": start,
                "end": end,
                "permissions": perms,
                "offset": offset,
            }
        )
    result: list[dict[str, Any]] = []
    for entry in grouped.values():
        entry["paths"] = sorted(entry["paths"])
        entry["segments"] = sorted(entry["segments"], key=lambda item: item["start"])
        result.append(entry)
    return sorted(result, key=lambda item: (item["device"], item["inode"]))


def _mapped_elf_metadata(mapping: Mapping[str, Any]) -> dict[str, Any] | None:
    if not any("x" in segment["permissions"] for segment in mapping["segments"]):
        return None
    errors: list[str] = []
    for path in mapping["paths"]:
        try:
            fd = os.open(path, os.O_RDONLY | O_CLOEXEC)
        except OSError as exc:
            errors.append(f"{path}:{exc.errno}")
            continue
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_dev != mapping["device"]
                or info.st_ino != mapping["inode"]
            ):
                errors.append(f"{path}:identity")
                continue
            return parse_elf64_dynamic_fd(fd, f"mapped ELF:{path}")
        except SmokeError as exc:
            errors.append(f"{path}:{exc}")
        finally:
            os.close(fd)
    if any(".so" in PurePosixPath(path).name for path in mapping["paths"]):
        raise SmokeError(
            "could not bind mapped DSO bytes to maps dev+ino: " + ";".join(errors)
        )
    return None


def _path_under_fixed_system_dso_roots(path: str) -> bool:
    if not os.path.isabs(path) or not os.path.exists(path):
        return False
    resolved = Path(path).resolve()
    for root_text in SYSTEM_DSO_ROOTS:
        root = Path(root_text)
        if root.exists():
            resolved_root = root.resolve()
            if resolved == resolved_root or resolved.is_relative_to(resolved_root):
                return True
    return False


def _validate_private_elf_search_paths(
    owner_relative: str, dynamic: Mapping[str, Any]
) -> list[dict[str, str]]:
    if dynamic["rpath"] and dynamic["runpath"]:
        raise SmokeError(f"private ELF has both RPATH and RUNPATH: {owner_relative}")
    evidence: list[dict[str, str]] = []
    owner_parent = list(PurePosixPath(owner_relative).parent.parts)
    if owner_parent == ["."]:
        owner_parent = []
    for tag in ("rpath", "runpath"):
        for component in dynamic[tag]:
            if not component:
                raise SmokeError(f"private ELF has empty {tag} component: {owner_relative}")
            origin_prefix = None
            for candidate in ("$ORIGIN", "${ORIGIN}"):
                if component == candidate or component.startswith(candidate + "/"):
                    origin_prefix = candidate
                    break
            if origin_prefix is not None:
                suffix = component[len(origin_prefix) :].lstrip("/")
                stack = list(owner_parent)
                for part in (PurePosixPath(suffix).parts if suffix else ()):
                    if part in {"", "."}:
                        continue
                    if part == "..":
                        if not stack:
                            raise SmokeError(
                                f"private ELF $ORIGIN search escapes private root: "
                                f"{owner_relative}:{component}"
                            )
                        stack.pop()
                    else:
                        stack.append(part)
                evidence.append(
                    {
                        "tag": tag,
                        "raw": component,
                        "resolved_private_relative_directory": (
                            "/".join(stack) if stack else "."
                        ),
                    }
                )
                continue
            if component.startswith("/") and _path_under_fixed_system_dso_roots(
                component
            ):
                evidence.append(
                    {
                        "tag": tag,
                        "raw": component,
                        "resolved_fixed_system_directory": os.fspath(
                            Path(component).resolve()
                        ),
                    }
                )
                continue
            raise SmokeError(
                f"private ELF search path is neither bounded $ORIGIN nor fixed system root: "
                f"{owner_relative}:{component}"
            )
    return evidence


class PrivateElfClosure:
    """Hold, preload, and map-audit the private ELF dependency closure."""

    def __init__(
        self,
        private_fd: int,
        files: Mapping[str, Mapping[str, Any]],
        guard: RecursiveInotifyGuard,
    ) -> None:
        if not sys.platform.startswith("linux"):
            raise SmokeError("private ELF closure requires Linux")
        self.private_fd = private_fd
        self.files = files
        self.guard = guard
        self.entries: dict[str, dict[str, Any]] = {}
        self.handles: list[Any] = []
        self.required_extensions: set[str] = set()
        self.latest_map_evidence: dict[str, Any] = {}
        try:
            private_root_link = os.readlink(f"/proc/self/fd/{private_fd}")
            if not os.path.isabs(private_root_link) or private_root_link.endswith(
                " (deleted)"
            ):
                raise SmokeError("held private runtime has no stable absolute proc path")
            self.private_root_realpath = Path(private_root_link).resolve()
            for relative in sorted(files):
                if not _looks_like_elf_dso(relative):
                    continue
                fd = open_relative_regular(
                    private_fd, relative, f"private ELF held open:{relative}"
                )
                info = os.fstat(fd)
                identity = fstat_identity(info)
                digest, size = digest_fd(fd, f"private ELF held hash:{relative}")
                record = files[relative]
                if (
                    digest != record["sha256"]
                    or size != record["size_bytes"]
                    or f"{stat.S_IMODE(info.st_mode):04o}" != record["mode"]
                ):
                    os.close(fd)
                    raise SmokeError(f"private ELF differs from manifest: {relative}")
                dynamic = parse_elf64_dynamic_fd(fd, f"private ELF:{relative}")
                dynamic["validated_search_paths"] = (
                    _validate_private_elf_search_paths(relative, dynamic)
                )
                self.entries[relative] = {
                    "fd": fd,
                    "identity": identity,
                    "sha256": digest,
                    "size_bytes": size,
                    "dynamic": dynamic,
                }
            if not self.entries:
                raise SmokeError("private runtime manifest contains no ELF shared objects")
            self.alias_to_relative: dict[str, str] = {}
            for relative, entry in self.entries.items():
                aliases = {PurePosixPath(relative).name}
                if entry["dynamic"]["soname"] is not None:
                    aliases.add(entry["dynamic"]["soname"])
                for alias in aliases:
                    prior = self.alias_to_relative.get(alias)
                    if prior is not None and prior != relative:
                        raise SmokeError(
                            f"ambiguous private ELF SONAME/basename: {alias}"
                        )
                    self.alias_to_relative[alias] = relative
            relations: list[dict[str, str]] = []
            preload: set[str] = set()
            rpath_relations: list[dict[str, str]] = []
            for owner, entry in self.entries.items():
                dynamic = entry["dynamic"]
                for needed in dynamic["needed"]:
                    target = self.alias_to_relative.get(needed)
                    if target is None:
                        continue
                    relation = {"owner": owner, "needed": needed, "target": target}
                    relations.append(relation)
                    preload.add(target)
                    if any(
                        "$ORIGIN" in value
                        for value in [*dynamic["rpath"], *dynamic["runpath"]]
                    ):
                        rpath_relations.append(relation)
            if not preload:
                raise SmokeError("private ELF closure contains no private DT_NEEDED edge")
            if not rpath_relations:
                raise SmokeError(
                    "private ELF closure has no $ORIGIN RPATH/RUNPATH dependency edge"
                )
            self.private_needed_relations = relations
            self.rpath_relations = rpath_relations
            self.preload_paths = sorted(preload)
            self.baseline_maps = read_proc_self_maps()
            self.baseline_identities = {
                (item["device"], item["inode"]) for item in self.baseline_maps
            }
            self._reject_baseline_private_alias_conflicts()
        except BaseException:
            self.close()
            raise

    def _reject_baseline_private_alias_conflicts(self) -> None:
        private_identities = {
            (entry["identity"]["device"], entry["identity"]["inode"])
            for entry in self.entries.values()
        }
        for mapping in self.baseline_maps:
            identity = (mapping["device"], mapping["inode"])
            if identity in private_identities:
                continue
            for mapped_path in mapping["paths"]:
                resolved = Path(mapped_path).resolve()
                if resolved == self.private_root_realpath or resolved.is_relative_to(
                    self.private_root_realpath
                ):
                    raise SmokeError(
                        "unmanifested private-tree ELF was mapped before smoke imports"
                    )
            metadata = _mapped_elf_metadata(mapping)
            if metadata is None:
                continue
            aliases = {PurePosixPath(path).name for path in mapping["paths"]}
            if metadata["soname"] is not None:
                aliases.add(metadata["soname"])
            collisions = sorted(set(aliases) & set(self.alias_to_relative))
            if collisions:
                raise SmokeError(
                    "private SONAME/basename already mapped from an external inode: "
                    + ",".join(collisions)
                )

    def _verify_entry(self, relative: str, phase: str) -> None:
        entry = self.entries.get(relative)
        if entry is None:
            raise SmokeError(f"{phase}: ELF absent from held closure: {relative}")
        info = os.fstat(entry["fd"])
        if not same_identity(info, entry["identity"]):
            raise SmokeError(f"{phase}: held ELF identity changed: {relative}")
        digest, size = digest_fd(entry["fd"], f"{phase}:{relative}")
        if digest != entry["sha256"] or size != entry["size_bytes"]:
            raise SmokeError(f"{phase}: held ELF bytes changed: {relative}")
        named = open_relative_regular(
            self.private_fd, relative, f"{phase}:named ELF:{relative}"
        )
        try:
            if not same_identity(os.fstat(named), entry["identity"]):
                raise SmokeError(f"{phase}: ELF path changed inode: {relative}")
            named_digest, named_size = digest_fd(named, f"{phase}:named hash:{relative}")
            if named_digest != entry["sha256"] or named_size != entry["size_bytes"]:
                raise SmokeError(f"{phase}: named ELF bytes changed: {relative}")
        finally:
            os.close(named)

    def verify_all_held(self, phase: str) -> None:
        for relative in sorted(self.entries):
            self._verify_entry(relative, phase)

    def preload(self) -> None:
        try:
            mode = getattr(os, "RTLD_NOW", 2) | getattr(ctypes, "RTLD_GLOBAL", 0x100)
            for relative in self.preload_paths:
                self.guard.assert_clean(f"private DSO preload pre:{relative}")
                self._verify_entry(relative, "private DSO preload pre")
                proc_directory_semantic_path = (
                    f"/proc/self/fd/{self.private_fd}/{relative}"
                )
                try:
                    handle = ctypes.CDLL(proc_directory_semantic_path, mode=mode)
                except OSError as exc:
                    raise SmokeError(
                        "private DSO preload through directory-semantic proc path failed: "
                        f"{relative}: {exc}"
                    ) from exc
                self.handles.append(handle)
                self._verify_entry(relative, "private DSO preload post")
                self.guard.assert_clean(f"private DSO preload post:{relative}")
            self.verify_all_held("private DSO closure post-preload")
            self.latest_map_evidence = self.verify_mappings(
                set(self.preload_paths), "private DSO post-preload maps"
            )
        except BaseException:
            self.close()
            raise

    def before_extension_load(self, relative: str, identity: Mapping[str, Any]) -> None:
        self._verify_entry(relative, "native extension pre-load")
        entry = self.entries[relative]
        if (
            entry["identity"]["device"] != identity["device"]
            or entry["identity"]["inode"] != identity["inode"]
        ):
            raise SmokeError("native extension loader FD differs from ELF closure FD")
        self.guard.assert_clean(f"native extension closure pre:{relative}")

    def after_extension_load(self, relative: str) -> None:
        self.required_extensions.add(relative)
        self._verify_entry(relative, "native extension post-load")
        self.guard.assert_clean(f"native extension closure post:{relative}")
        self.latest_map_evidence = self.verify_mappings(
            set(self.preload_paths) | self.required_extensions,
            f"native extension mapped:{relative}",
        )

    def verify_mappings(
        self, required_private_paths: set[str], phase: str
    ) -> dict[str, Any]:
        mappings = read_proc_self_maps()
        mapping_by_identity = {
            (item["device"], item["inode"]): item for item in mappings
        }
        private_by_identity = {
            (entry["identity"]["device"], entry["identity"]["inode"]): relative
            for relative, entry in self.entries.items()
        }
        for relative in sorted(required_private_paths):
            entry = self.entries.get(relative)
            if entry is None:
                raise SmokeError(f"{phase}: required private ELF is unmanifested")
            identity = (
                entry["identity"]["device"], entry["identity"]["inode"]
            )
            if identity not in mapping_by_identity:
                raise SmokeError(f"{phase}: required private ELF inode is not mapped: {relative}")

        external_elf: dict[tuple[int, int], dict[str, Any]] = {}
        private_mappings: list[dict[str, Any]] = []
        new_system_mappings: list[dict[str, Any]] = []
        for identity, mapping in mapping_by_identity.items():
            private_relative = private_by_identity.get(identity)
            if private_relative is not None:
                entry = self.entries[private_relative]
                private_mappings.append(
                    {
                        "relative_path": private_relative,
                        "device": identity[0],
                        "inode": identity[1],
                        "sha256": entry["sha256"],
                        "soname": entry["dynamic"]["soname"],
                        "paths": mapping["paths"],
                    }
                )
                if mapping["deleted"]:
                    raise SmokeError(f"{phase}: mapped private ELF is deleted")
                continue
            for mapped_path in mapping["paths"]:
                try:
                    resolved_mapped_path = Path(mapped_path).resolve()
                except OSError as exc:
                    raise SmokeError(
                        f"{phase}: mapped path cannot be resolved: {mapped_path}: {exc}"
                    ) from exc
                if (
                    resolved_mapped_path == self.private_root_realpath
                    or resolved_mapped_path.is_relative_to(
                        self.private_root_realpath
                    )
                ):
                    raise SmokeError(
                        f"{phase}: private-tree mapping is absent from held manifest: "
                        f"{mapped_path}"
                    )
            metadata = _mapped_elf_metadata(mapping)
            if metadata is None:
                continue
            external_elf[identity] = metadata
            aliases = {PurePosixPath(path).name for path in mapping["paths"]}
            if metadata["soname"] is not None:
                aliases.add(metadata["soname"])
            for alias in aliases:
                expected_relative = self.alias_to_relative.get(alias)
                if expected_relative is not None:
                    raise SmokeError(
                        f"{phase}: private SONAME escaped to external inode: "
                        f"{alias} -> {mapping['paths']}"
                    )
            if identity not in self.baseline_identities:
                if mapping["deleted"] or not all(
                    _path_under_fixed_system_dso_roots(path)
                    for path in mapping["paths"]
                ):
                    raise SmokeError(
                        f"{phase}: new external DSO mapping escaped fixed system roots"
                    )
                new_system_mappings.append(
                    {
                        "device": identity[0],
                        "inode": identity[1],
                        "paths": mapping["paths"],
                        "soname": metadata["soname"],
                    }
                )

        required_owners = set(required_private_paths)
        required_owners.update(self.preload_paths)
        system_needed = {
            needed
            for owner in required_owners
            for needed in self.entries[owner]["dynamic"]["needed"]
            if needed not in self.alias_to_relative
        }
        system_providers: list[dict[str, Any]] = []
        for needed in sorted(system_needed):
            providers: list[tuple[tuple[int, int], Mapping[str, Any], Mapping[str, Any]]] = []
            for identity, metadata in external_elf.items():
                mapping = mapping_by_identity[identity]
                aliases = {PurePosixPath(path).name for path in mapping["paths"]}
                if metadata["soname"] is not None:
                    aliases.add(metadata["soname"])
                if needed in aliases:
                    providers.append((identity, mapping, metadata))
            if len(providers) != 1:
                raise SmokeError(
                    f"{phase}: system DT_NEEDED provider is not unique: {needed}"
                )
            identity, mapping, metadata = providers[0]
            if mapping["deleted"] or not all(
                _path_under_fixed_system_dso_roots(path)
                for path in mapping["paths"]
            ):
                raise SmokeError(
                    f"{phase}: system DT_NEEDED provider escaped fixed roots: {needed}"
                )
            system_providers.append(
                {
                    "needed": needed,
                    "device": identity[0],
                    "inode": identity[1],
                    "paths": mapping["paths"],
                    "soname": metadata["soname"],
                }
            )
        return {
            "phase": phase,
            "private_mappings": sorted(
                private_mappings, key=lambda item: item["relative_path"]
            ),
            "new_system_dso_mappings": sorted(
                new_system_mappings, key=lambda item: (item["device"], item["inode"])
            ),
            "system_dependency_providers": system_providers,
            "system_dso_roots": list(SYSTEM_DSO_ROOTS),
            "same_soname_external_escape_absent": True,
        }

    def evidence(self) -> dict[str, Any]:
        return {
            "held_private_elf_count": len(self.entries),
            "held_private_elf_inventory": [
                {
                    "relative_path": relative,
                    "device": entry["identity"]["device"],
                    "inode": entry["identity"]["inode"],
                    "sha256": entry["sha256"],
                    "size_bytes": entry["size_bytes"],
                    "soname": entry["dynamic"]["soname"],
                    "needed": entry["dynamic"]["needed"],
                    "rpath": entry["dynamic"]["rpath"],
                    "runpath": entry["dynamic"]["runpath"],
                    "validated_search_paths": entry["dynamic"][
                        "validated_search_paths"
                    ],
                }
                for relative, entry in sorted(self.entries.items())
            ],
            "private_dt_needed_edges": self.private_needed_relations,
            "origin_rpath_private_edges": self.rpath_relations,
            "preloaded_private_dsos": [
                {
                    "relative_path": relative,
                    "device": self.entries[relative]["identity"]["device"],
                    "inode": self.entries[relative]["identity"]["inode"],
                    "sha256": self.entries[relative]["sha256"],
                    "soname": self.entries[relative]["dynamic"]["soname"],
                    "needed": self.entries[relative]["dynamic"]["needed"],
                    "rpath": self.entries[relative]["dynamic"]["rpath"],
                    "runpath": self.entries[relative]["dynamic"]["runpath"],
                    "validated_search_paths": self.entries[relative]["dynamic"][
                        "validated_search_paths"
                    ],
                    "load_path": (
                        f"/proc/self/fd/{self.private_fd}/{relative}"
                    ),
                }
                for relative in self.preload_paths
            ],
            "required_extension_paths": sorted(self.required_extensions),
            "maps": self.latest_map_evidence,
            "private_dso_preload_mode": "RTLD_NOW|RTLD_GLOBAL",
            "extension_origin_mode": "HELD_REGULAR_FILE_PROC_FD",
            "directory_semantic_preload_preserves_origin_rpath": True,
        }

    def close(self) -> None:
        for entry in getattr(self, "entries", {}).values():
            fd = entry.get("fd", -1)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                entry["fd"] = -1
        self.handles.clear()


class HeldVerifiedModuleLoader:
    """Execute source/bytecode/native code through a held verified file FD."""

    def __init__(
        self,
        finder: "HeldVerifiedRuntimeFinder",
        fullname: str,
        relative: str,
        kind: str,
        is_package: bool,
    ) -> None:
        self.finder = finder
        self.fullname = fullname
        self.relative = relative
        self.kind = kind
        self._is_package = is_package
        self.fd = open_relative_regular(
            finder.private_fd, relative, f"held import pre-open:{fullname}"
        )
        self.identity = fstat_identity(os.fstat(self.fd))
        record = finder.files.get(relative)
        if record is None:
            self.close()
            raise SmokeError(f"held import member absent from manifest: {fullname}")
        digest, size = digest_fd(self.fd, f"held import pre-hash:{fullname}")
        if (
            digest != record["sha256"]
            or size != record["size_bytes"]
            or f"{stat.S_IMODE(os.fstat(self.fd).st_mode):04o}" != record["mode"]
        ):
            self.close()
            raise SmokeError(f"held import pre-open evidence mismatch: {fullname}")
        self.expected_digest = digest
        self.expected_size = size
        self.data: bytes | None = None
        if kind in {"source", "bytecode"}:
            self.data = read_fd_bytes(
                self.fd, f"held import bytes:{fullname}", maximum_size=256 * 1024 * 1024
            )
            if hashlib.sha256(self.data).hexdigest() != digest:
                self.close()
                raise SmokeError(f"held import byte/hash mismatch: {fullname}")
        self.package_fd = -1
        if is_package:
            relative_parent = PurePosixPath(relative).parent
            package_relative = os.fspath(
                relative_parent.parent
                if relative_parent.name == "__pycache__"
                else relative_parent
            )
            self.package_fd = open_relative_directory(
                finder.private_fd,
                package_relative,
                f"held package directory:{fullname}",
            )
        if kind == "extension":
            self.origin = f"/proc/self/fd/{self.fd}"
            self._extension = importlib.machinery.ExtensionFileLoader(
                fullname, self.origin
            )
        else:
            self.origin = f"/proc/self/fd/{finder.private_fd}/{relative}"
            self._extension = None
        self.executed = False
        self.finder.loaders.append(self)

    def is_package(self, _fullname: str) -> bool:
        return self._is_package

    def create_module(self, spec: Any) -> Any:
        if self.kind != "extension":
            return None
        self._verify_held_and_named("native create_module pre")
        self.finder.guard.assert_clean(f"native create_module pre:{self.fullname}")
        if self.finder.elf_closure is not None:
            self.finder.elf_closure.before_extension_load(
                self.relative, self.identity
            )
        try:
            module = self._extension.create_module(spec)
        except BaseException as exc:
            raise SmokeError(
                "held-FD native extension load failed closed; no pathname fallback: "
                f"{self.fullname}: {type(exc).__name__}: {exc}"
            ) from exc
        self._verify_held_and_named("native create_module post")
        self.finder.guard.assert_clean(f"native create_module post:{self.fullname}")
        if self.finder.elf_closure is not None:
            self.finder.elf_closure.after_extension_load(self.relative)
        return module

    def exec_module(self, module: Any) -> None:
        self._verify_held_and_named("exec pre")
        self.finder.guard.assert_clean(f"module exec pre:{self.fullname}")
        if self.kind == "source":
            if self.data is None:
                raise SmokeError("internal held source bytes missing")
            code = compile(self.data, self.origin, "exec", dont_inherit=True)
            exec(code, module.__dict__)
        elif self.kind == "bytecode":
            if self.data is None or len(self.data) < 16:
                raise SmokeError(f"held bytecode header truncated: {self.fullname}")
            if self.data[:4] != importlib.util.MAGIC_NUMBER:
                raise SmokeError(f"held bytecode magic mismatch: {self.fullname}")
            flags = int.from_bytes(self.data[4:8], "little")
            if flags not in {0, 1, 3}:
                raise SmokeError(f"held bytecode flags unsupported: {self.fullname}")
            try:
                code = marshal.loads(self.data[16:])
            except (EOFError, ValueError, TypeError) as exc:
                raise SmokeError(f"held bytecode unmarshal failed: {self.fullname}") from exc
            if not isinstance(code, types.CodeType):
                raise SmokeError(f"held bytecode does not contain a code object: {self.fullname}")
            exec(code, module.__dict__)
        elif self.kind == "extension":
            try:
                self._extension.exec_module(module)
            except BaseException as exc:
                raise SmokeError(
                    "held-FD native extension execution failed closed; no pathname fallback: "
                    f"{self.fullname}: {type(exc).__name__}: {exc}"
                ) from exc
        else:
            raise SmokeError(f"unsupported held loader kind: {self.kind}")
        self.executed = True
        self._verify_held_and_named("exec post")
        self.finder.guard.assert_clean(f"module exec post:{self.fullname}")
        self.finder.loaded[self.fullname] = {
            "module": self.fullname,
            "relative_origin": self.relative,
            "execution_kind": self.kind,
            "held_device": self.identity["device"],
            "held_inode": self.identity["inode"],
            "sha256": self.expected_digest,
            "executed_from_held_verified_bytes_or_proc_fd": True,
        }

    def _verify_held_and_named(self, phase: str) -> None:
        info = os.fstat(self.fd)
        if not same_identity(info, self.identity):
            raise SmokeError(f"{phase}: held module identity changed: {self.fullname}")
        digest, size = digest_fd(self.fd, f"{phase}:{self.fullname}")
        if digest != self.expected_digest or size != self.expected_size:
            raise SmokeError(f"{phase}: held module bytes changed: {self.fullname}")
        named = open_relative_regular(
            self.finder.private_fd, self.relative, f"{phase} named path:{self.fullname}"
        )
        try:
            named_info = os.fstat(named)
        finally:
            os.close(named)
        if not same_identity(named_info, self.identity):
            raise SmokeError(
                f"{phase}: module path no longer names held inode: {self.fullname}"
            )

    def close(self) -> None:
        for attribute in ("package_fd", "fd"):
            fd = getattr(self, attribute, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, attribute, -1)


class HeldVerifiedRuntimeFinder:
    def __init__(
        self,
        private_fd: int,
        files: Mapping[str, Mapping[str, Any]],
        directories: set[str],
        guard: RecursiveInotifyGuard,
        elf_closure: PrivateElfClosure | None = None,
    ) -> None:
        self.private_fd = private_fd
        self.files = files
        self.directories = directories
        self.guard = guard
        self.elf_closure = elf_closure
        self.loaders: list[HeldVerifiedModuleLoader] = []
        self.namespace_fds: list[int] = []
        self.loaded: dict[str, dict[str, Any]] = {}
        self.namespaces: dict[str, str] = {}

    def _bytecode_candidates(self, base: str, *, package: bool) -> list[str]:
        if package:
            prefix = f"{base}/__pycache__/__init__."
        else:
            parent = os.fspath(PurePosixPath(base).parent)
            leaf = PurePosixPath(base).name
            prefix = f"{parent + '/' if parent != '.' else ''}__pycache__/{leaf}."
        return sorted(
            path for path in self.files if path.startswith(prefix) and path.endswith(".pyc")
        )

    def find_spec(self, fullname: str, _path: Any = None, _target: Any = None) -> Any:
        base = fullname.replace(".", "/")
        candidates: list[tuple[str, str, bool]] = []
        package_source = f"{base}/__init__.py"
        module_source = f"{base}.py"
        package_pyc = f"{base}/__init__.pyc"
        module_pyc = f"{base}.pyc"
        if package_source in self.files:
            candidates.append((package_source, "source", True))
        elif package_pyc in self.files:
            candidates.append((package_pyc, "bytecode", True))
        else:
            cached = self._bytecode_candidates(base, package=True)
            if len(cached) > 1:
                raise SmokeError(f"ambiguous held package bytecode candidates: {fullname}")
            if cached:
                candidates.append((cached[0], "bytecode", True))
        # FileFinder checks a package directory before a same-name module file.
        # For a non-package, CPython's supported-loader order gives native
        # extensions precedence over source and bytecode.
        if not candidates:
            extension_candidates = [
                f"{base}{suffix}"
                for suffix in importlib.machinery.EXTENSION_SUFFIXES
                if f"{base}{suffix}" in self.files
            ]
            if len(extension_candidates) > 1:
                raise SmokeError(f"ambiguous held native candidates: {fullname}")
            if extension_candidates:
                candidates.append((extension_candidates[0], "extension", False))
            elif module_source in self.files:
                candidates.append((module_source, "source", False))
            elif module_pyc in self.files:
                candidates.append((module_pyc, "bytecode", False))
            else:
                cached = self._bytecode_candidates(base, package=False)
                if len(cached) > 1:
                    raise SmokeError(f"ambiguous held module bytecode candidates: {fullname}")
                if cached:
                    candidates.append((cached[0], "bytecode", False))
        if len(candidates) > 1:
            raise SmokeError(f"ambiguous held import candidates: {fullname}: {candidates}")
        if candidates:
            relative, kind, is_package = candidates[0]
            loader = HeldVerifiedModuleLoader(
                self, fullname, relative, kind, is_package
            )
            spec = importlib.util.spec_from_loader(
                fullname, loader, origin=loader.origin, is_package=is_package
            )
            if spec is None:
                loader.close()
                raise SmokeError(f"could not construct held import spec: {fullname}")
            spec.has_location = True
            if is_package:
                spec.submodule_search_locations = [f"/proc/self/fd/{loader.package_fd}"]
            return spec
        if base in self.directories:
            directory_fd = open_relative_directory(
                self.private_fd, base, f"held namespace package:{fullname}"
            )
            self.namespace_fds.append(directory_fd)
            spec = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [f"/proc/self/fd/{directory_fd}"]
            self.namespaces[fullname] = base
            return spec
        return None

    def close(self) -> None:
        for loader in reversed(self.loaders):
            loader.close()
        for fd in self.namespace_fds:
            os.close(fd)
        self.namespace_fds.clear()


def import_and_verify(
    private_fd: int,
    runtime: Mapping[str, Any],
    scratch_fd: int,
    private_structural_records: Sequence[Mapping[str, Any]],
    guard: RecursiveInotifyGuard,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if "numpy" in sys.modules or "matplotlib" in sys.modules:
        raise SmokeError("numpy/matplotlib must not be pre-imported")
    baseline_path, stdlib_roots = _stdlib_roots_before_private_insert()
    before_modules = set(sys.modules)
    private_proc = f"/proc/self/fd/{private_fd}"
    scratch_proc = f"/proc/self/fd/{scratch_fd}"
    # Do not expose the private tree to PathFinder.  The held verifier below is
    # the sole private import authority; stdlib retains its isolated baseline.
    sys.path[:] = baseline_path
    for name in ENVIRONMENT_KEYS:
        os.environ[name] = scratch_proc

    directories = {
        item["relative_path"]
        for item in private_structural_records
        if item["kind"] == "directory" and item["relative_path"] != "."
    }
    elf_closure = PrivateElfClosure(private_fd, runtime["files"], guard)
    elf_closure.preload()
    finder = HeldVerifiedRuntimeFinder(
        private_fd,
        runtime["files"],
        directories,
        guard,
        elf_closure,
    )
    meta_before = list(sys.meta_path)
    sys.meta_path.insert(0, finder)
    try:
        imported: dict[str, Any] = {}
        for name in ("numpy", "matplotlib"):
            distribution = runtime["distributions"][name]
            module = importlib.import_module(name)
            version = getattr(module, "__version__", None)
            if type(version) is not str or version != distribution["version"]:
                raise SmokeError(f"imported version mismatch: {name}")
            evidence = finder.loaded.get(name)
            if evidence is None:
                raise SmokeError(f"top-level import bypassed held verifier: {name}")
            if evidence["relative_origin"] != distribution["import_relative_path"]:
                raise SmokeError(f"imported top-level relative origin mismatch: {name}")
            imported[name] = {
                "version": version,
                "relative_origin": evidence["relative_origin"],
                "execution_kind": evidence["execution_kind"],
            }

        if sys.path != baseline_path:
            raise SmokeError("sys.path changed during imports")
        if sys.meta_path != [finder, *meta_before]:
            raise SmokeError("sys.meta_path changed during imports")
        guard.assert_clean("all imports complete")
        stdlib_module_count = 0
        for module_name in sorted(set(sys.modules) - before_modules):
            if module_name in finder.loaded or module_name in finder.namespaces:
                continue
            module = sys.modules.get(module_name)
            origin = getattr(module, "__file__", None) if module is not None else None
            if origin is None or origin in {"built-in", "frozen"}:
                continue
            if type(origin) is not str:
                raise SmokeError(f"new module has non-string origin: {module_name}")
            if origin.startswith(private_proc + "/") or origin.startswith("/proc/self/fd/"):
                raise SmokeError(f"private module bypassed held loader: {module_name}")
            parts = {part.lower() for part in PurePosixPath(origin).parts}
            if "site-packages" in parts or "dist-packages" in parts:
                raise SmokeError(f"new module escaped to external package site: {module_name}")
            if not os.path.isabs(origin) or not _path_is_below(Path(origin), stdlib_roots):
                raise SmokeError(f"new module origin is outside private runtime/stdlib: {module_name}")
            stdlib_module_count += 1
        if any(not loader.executed for loader in finder.loaders):
            pending = [loader.fullname for loader in finder.loaders if not loader.executed]
            raise SmokeError(f"held loaders created but not executed: {pending}")
        loaded_evidence = [finder.loaded[name] for name in sorted(finder.loaded)]
        execution_kind_counts = {
            kind: sum(
                item["execution_kind"] == kind for item in loaded_evidence
            )
            for kind in ("source", "bytecode", "extension")
        }
        if execution_kind_counts["extension"] < 1:
            raise SmokeError("NumPy smoke loaded no held-FD native extension module")
        elf_closure.verify_all_held("all imports complete ELF revalidation")
        elf_closure.latest_map_evidence = elf_closure.verify_mappings(
            set(elf_closure.preload_paths) | elf_closure.required_extensions,
            "all imports complete maps",
        )
        return imported, {
            "private_module_origin_count": len(finder.loaded),
            "private_namespace_count": len(finder.namespaces),
            "stdlib_module_origin_count": stdlib_module_count,
            "private_module_origins": loaded_evidence,
            "execution_kind_counts": execution_kind_counts,
            "runtime_sys_path": list(baseline_path),
            "private_runtime_exposed_to_pathfinder": False,
            "scratch_environment": {name: scratch_proc for name in ENVIRONMENT_KEYS},
            "source_and_bytecode_execution_from_held_verified_bytes": True,
            "native_extension_execution_via_held_proc_fd": True,
            "private_elf_dependency_closure": elf_closure.evidence(),
            "native_pathname_fallback_allowed": False,
            "concurrent_same_host_mutation_detection": (
                "MASK_SCOPED_DIRECTORY_AND_REGULAR_INODE_INOTIFY_PLUS_"
                "SETUP_DIGEST_AND_PRE_POST_INVENTORY"
            ),
            "global_immutability_or_no_side_effect_claim_made": False,
        }
    finally:
        if sys.meta_path and sys.meta_path[0] is finder:
            del sys.meta_path[0]
        else:
            sys.meta_path[:] = meta_before
        finder.close()
        elf_closure.close()


def _revalidate_named_child(root_fd: int, name: str, held_fd: int, label: str) -> None:
    held = os.fstat(held_fd)
    try:
        current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except OSError as exc:
        raise SmokeError(f"{label}: root child path disappeared: {exc}") from exc
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        raise SmokeError(f"{label}: root child path no longer names held inode")


def _exact_full_file_identity(value: Any, label: str) -> dict[str, Any]:
    item = exact_object(
        value,
        {
            "device", "inode", "size_bytes", "mtime_ns", "ctime_ns",
            "mode", "nlink",
        },
        label,
    )
    result: dict[str, Any] = {
        name: exact_int(item[name], f"{label}.{name}")
        for name in (
            "device", "inode", "size_bytes", "mtime_ns", "ctime_ns", "nlink"
        )
    }
    mode = exact_string(item["mode"], f"{label}.mode")
    if re.fullmatch(r"0[0-7]{3}", mode) is None:
        raise SmokeError(f"{label}.mode: canonical four-digit octal required")
    result["mode"] = mode
    return result


def _stat_full_file_identity(value: os.stat_result) -> dict[str, Any]:
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "size_bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
        "mode": f"{stat.S_IMODE(value.st_mode):04o}",
        "nlink": value.st_nlink,
    }


def _validate_held_bootstrap_context(
    smoke_argv: Sequence[str], context: Mapping[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    top = exact_object(
        context,
        {
            "protocol", "interpreter_fd", "smoke_source_fd",
            "interpreter_identity", "interpreter_sha256",
            "smoke_source_identity", "smoke_source_sha256",
            "original_smoke_evidence_path", "bootstrap_sha256",
            "actual_cmdline", "authorization_fd", "authorization_identity",
            "authorization_sha256",
        },
        "held bootstrap context",
    )
    if top["protocol"] != HELD_SMOKE_BOOTSTRAP_PROTOCOL:
        raise SmokeError("held bootstrap protocol mismatch")
    if (
        exact_int(top["interpreter_fd"], "held bootstrap interpreter_fd")
        != INTERPRETER_FD
        or exact_int(top["smoke_source_fd"], "held bootstrap smoke_source_fd")
        != SMOKE_SOURCE_FD
    ):
        raise SmokeError("held bootstrap fixed FD mismatch")
    if type(top["authorization_fd"]) is not int or top["authorization_fd"] < 0:
        raise SmokeError("held bootstrap authorization FD is invalid")
    if __file__ != f"/proc/self/fd/{SMOKE_SOURCE_FD}" or __name__ != (
        "_result_free_runtime_smoke_v10_held_bytes__"
    ):
        raise SmokeError("smoke module was not compiled from the held FD origin")
    for fd, label in (
        (INTERPRETER_FD, "interpreter"), (SMOKE_SOURCE_FD, "smoke source")
    ):
        try:
            fd_flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            status_flags = require_readonly_fd(fd, f"held {label}")
        except OSError as exc:
            raise SmokeError(f"held {label} FD is absent: {exc}") from exc
        if fd_flags & fcntl.FD_CLOEXEC:
            raise SmokeError(f"held {label} FD unexpectedly has CLOEXEC")

    interpreter_identity = _exact_full_file_identity(
        top["interpreter_identity"], "held bootstrap interpreter identity"
    )
    interpreter_before = os.fstat(INTERPRETER_FD)
    if not stat.S_ISREG(interpreter_before.st_mode) or _stat_full_file_identity(
        interpreter_before
    ) != interpreter_identity:
        raise SmokeError("held interpreter identity differs from bootstrap context")
    interpreter_sha, _ = digest_fd(INTERPRETER_FD, "held interpreter entry recheck")
    if interpreter_sha != exact_sha(
        top["interpreter_sha256"], "held bootstrap interpreter SHA"
    ) or interpreter_sha != args.expected_python_sha256:
        raise SmokeError("held interpreter SHA differs from context/CLI")
    executable_fd = os.open("/proc/self/exe", os.O_RDONLY | O_CLOEXEC)
    try:
        executable_info = os.fstat(executable_fd)
        executable_sha, _ = digest_fd(executable_fd, "/proc/self/exe entry recheck")
    finally:
        os.close(executable_fd)
    if (
        executable_info.st_dev,
        executable_info.st_ino,
    ) != (interpreter_before.st_dev, interpreter_before.st_ino) or (
        executable_sha != interpreter_sha
    ):
        raise SmokeError("held FD197 is not the running /proc/self/exe bytes")

    source_identity = exact_held_smoke_source_identity(
        top["smoke_source_identity"], "held bootstrap smoke source identity"
    )
    source_before = os.fstat(SMOKE_SOURCE_FD)
    if not stat.S_ISREG(source_before.st_mode) or _stat_full_file_identity(
        source_before
    ) != source_identity:
        raise SmokeError("held smoke source identity differs from bootstrap context")
    source_sha, _ = digest_fd(SMOKE_SOURCE_FD, "held smoke source entry recheck")
    if source_sha != exact_sha(
        top["smoke_source_sha256"], "held bootstrap smoke source SHA"
    ) or source_sha != args.expected_smoke_script_sha256:
        raise SmokeError("held smoke source SHA differs from context/CLI")
    original_path = canonical_absolute_path(
        top["original_smoke_evidence_path"],
        "held bootstrap original smoke evidence path",
    )
    if exact_sha(top["bootstrap_sha256"], "held bootstrap SHA") != (
        HELD_SMOKE_BOOTSTRAP_SHA256
    ):
        raise SmokeError("held bootstrap SHA differs from frozen constant")
    actual_cmdline = exact_process_argv(
        top["actual_cmdline"], "held bootstrap actual cmdline"
    )
    if actual_cmdline != read_proc_cmdline():
        raise SmokeError("held bootstrap context cmdline differs from /proc")
    expected_cmdline = build_held_smoke_argv(
        source_identity, source_sha, original_path, list(smoke_argv)
    )
    if actual_cmdline != expected_cmdline:
        raise SmokeError("held bootstrap context is not the exact rebuilt argv")
    authorization_identity = _exact_full_file_identity(
        top["authorization_identity"], "held bootstrap authorization identity"
    )
    authorization_info = os.fstat(top["authorization_fd"])
    if not stat.S_ISREG(authorization_info.st_mode) or _stat_full_file_identity(
        authorization_info
    ) != authorization_identity:
        raise SmokeError("held authorization identity differs from bootstrap context")
    if exact_sha(top["authorization_sha256"], "held bootstrap authorization SHA") != (
        args.trusted_smoke_authorization_sha256
    ):
        raise SmokeError("held authorization SHA differs from CLI")
    return {
        **top,
        "interpreter_identity": interpreter_identity,
        "smoke_source_identity": source_identity,
        "authorization_identity": authorization_identity,
        "actual_cmdline": actual_cmdline,
        "original_smoke_evidence_path": original_path,
    }


def _authenticated_smoke_main(
    args: argparse.Namespace,
    actual_cmdline: list[str],
    smoke_argv: Sequence[str],
    bootstrap_context: Mapping[str, Any],
) -> int:
    _require_platform_fd_features()

    auth_parent_fd, auth_name = open_absolute_parent(
        args.smoke_authorization, "smoke authorization evidence parent"
    )
    auth_fd = os.dup(bootstrap_context["authorization_fd"])
    build_parent_fd = build_fd = -1
    outer_launch_parent_fd = outer_launch_fd = -1
    outer_launch_name = ""
    lock_parent_fd = lock_fd = -1
    journal_parent_fd = journal_fd = -1
    script_parent_fd = script_fd = -1
    root_parent_fd = root_fd = scratch_parent_fd = scratch_fd = -1
    bundle_fd = private_fd = manifest_fd = -1
    support_fds: dict[str, int] = {}
    guard: RecursiveInotifyGuard | None = None
    try:
        auth_info = os.fstat(auth_fd)
        if stat.S_IMODE(auth_info.st_mode) != 0o444:
            raise SmokeError("smoke authorization mode must be 0444")
        auth_raw = read_fd_bytes(auth_fd, "smoke authorization", maximum_size=16 * 1024 * 1024)
        auth_sha = hashlib.sha256(auth_raw).hexdigest()
        if auth_sha != args.trusted_smoke_authorization_sha256:
            raise SmokeError("single-open smoke authorization SHA mismatch")
        auth_json = strict_json_bytes(auth_raw, "smoke authorization")
        if _stat_full_file_identity(auth_info) != bootstrap_context["authorization_identity"]:
            raise SmokeError("duplicated held authorization identity mismatch")
        auth = validate_smoke_authorization(
            auth_json, args, actual_cmdline, bootstrap_context, smoke_argv
        )

        script_parent_fd, script_fd, _ = open_absolute_regular(
            auth["paths"]["smoke_script"], "smoke script"
        )
        script_sha, _ = digest_fd(script_fd, "smoke script")
        if script_sha != args.expected_smoke_script_sha256:
            raise SmokeError("original smoke evidence path SHA mismatch")
        script_info = os.fstat(script_fd)
        held_script_info = os.fstat(SMOKE_SOURCE_FD)
        if (script_info.st_dev, script_info.st_ino) != (
            held_script_info.st_dev, held_script_info.st_ino
        ):
            raise SmokeError("original smoke evidence path no longer names held source")

        executable_fd = os.open("/proc/self/exe", os.O_RDONLY | O_CLOEXEC)
        try:
            executable_sha, _ = digest_fd(executable_fd, "source Python executable")
        finally:
            os.close(executable_fd)
        if executable_sha != args.expected_python_sha256:
            raise SmokeError("source Python executable SHA mismatch")
        if Path(os.readlink("/proc/self/exe")).resolve() != Path(auth["paths"]["source_python"]).resolve():
            raise SmokeError("source Python executable realpath mismatch")

        build_parent_fd, build_fd, _ = open_absolute_regular(
            args.build_pass_receipt, "build PASS receipt"
        )
        build_info = os.fstat(build_fd)
        if stat.S_IMODE(build_info.st_mode) != 0o444:
            raise SmokeError("build PASS receipt mode must be 0444")
        build_raw = read_fd_bytes(build_fd, "build PASS receipt", maximum_size=64 * 1024 * 1024)
        build_sha = hashlib.sha256(build_raw).hexdigest()
        if build_sha != args.trusted_build_pass_receipt_sha256:
            raise SmokeError("single-open build PASS receipt SHA mismatch")
        build_json = strict_json_bytes(build_raw, "build PASS receipt")
        build = validate_build_receipt(build_json, args, auth)
        outer_launch_parent_fd, outer_launch_fd, outer_launch_name = (
            open_absolute_regular(
                build["trusted_launch"]["outer_launch_receipt_path"],
                "outer preflight launch receipt",
            )
        )
        outer_launch_info = os.fstat(outer_launch_fd)
        if stat.S_IMODE(outer_launch_info.st_mode) != 0o444:
            raise SmokeError("outer preflight launch receipt mode must be 0444")
        outer_launch_raw = read_fd_bytes(
            outer_launch_fd,
            "outer preflight launch receipt",
            maximum_size=64 * 1024 * 1024,
        )
        outer_launch_sha = hashlib.sha256(outer_launch_raw).hexdigest()
        if outer_launch_sha != build["trusted_launch"]["outer_launch_receipt_sha256"]:
            raise SmokeError("outer preflight launch receipt SHA mismatch")
        lock_parent_fd, lock_fd, lock_name = open_absolute_regular(
            build["journal"]["lock_path"], "build journal lock"
        )
        lock_info = os.fstat(lock_fd)
        if (
            lock_info.st_dev != build["journal"]["lock_device"]
            or lock_info.st_ino != build["journal"]["lock_inode"]
            or stat.S_IMODE(lock_info.st_mode) != 0o600
        ):
            raise SmokeError("build journal lock held identity/mode mismatch")
        journal_parent_fd, journal_fd, journal_name = open_absolute_directory(
            build["journal"]["directory"], "build journal directory"
        )
        journal_info = os.fstat(journal_fd)
        if (
            journal_info.st_dev != build["journal"]["directory_device"]
            or journal_info.st_ino != build["journal"]["directory_inode"]
            or stat.S_IMODE(journal_info.st_mode) != 0o700
        ):
            raise SmokeError("build journal directory held identity/mode mismatch")
        journal_parent_info = os.fstat(journal_parent_fd)
        if (
            journal_parent_info.st_dev != build["journal"]["parent_device"]
            or journal_parent_info.st_ino != build["journal"]["parent_inode"]
        ):
            raise SmokeError("build journal parent held identity mismatch")
        lock_parent_info = os.fstat(lock_parent_fd)
        if (lock_parent_info.st_dev, lock_parent_info.st_ino) != (
            journal_info.st_dev,
            journal_info.st_ino,
        ):
            raise SmokeError("lock parent and held journal directory identities differ")
        _revalidate_named_child(journal_fd, "LOCK", lock_fd, "journal lock")

        # The ROOT FD is acquired before any ROOT child is opened and remains
        # held through every pre/post-import verification.
        root_parent_fd, root_fd, root_name = open_absolute_directory(
            args.final_root, "final ROOT"
        )
        root_info = os.fstat(root_fd)
        if not same_identity(root_info, auth["root_identity"]):
            raise SmokeError("held final ROOT identity mismatch")
        if stat.S_IMODE(root_info.st_mode) != 0o555:
            raise SmokeError("final ROOT mode must be 0555")
        root_parent_info = os.fstat(root_parent_fd)
        if (
            root_parent_info.st_dev != build["journal"]["parent_device"]
            or root_parent_info.st_ino != build["journal"]["parent_inode"]
        ):
            raise SmokeError("final ROOT parent identity differs from build journal binding")

        root_path = PurePosixPath(args.final_root)
        scratch_path = PurePosixPath(args.scratch_dir)
        if scratch_path == root_path or scratch_path.is_relative_to(root_path) or root_path.is_relative_to(scratch_path):
            raise SmokeError("scratch and final ROOT paths must be disjoint")
        scratch_parent_fd, scratch_fd, scratch_name = open_absolute_directory(
            args.scratch_dir, "scratch directory"
        )
        scratch_info = os.fstat(scratch_fd)
        if not same_identity(scratch_info, auth["scratch_identity"]):
            raise SmokeError("held scratch identity mismatch")
        if scratch_info.st_uid != os.geteuid() or stat.S_IMODE(scratch_info.st_mode) != 0o700:
            raise SmokeError("scratch must be owned by euid with mode 0700")
        if (scratch_info.st_dev, scratch_info.st_ino) == (root_info.st_dev, root_info.st_ino):
            raise SmokeError("scratch aliases final ROOT")

        bundle_fd = open_child(root_fd, "bundle", "ROOT/bundle", directory=True)
        private_fd = open_child(
            root_fd,
            "private_runtime_site_packages",
            "ROOT/private_runtime_site_packages",
            directory=True,
        )
        manifest_fd = open_child(
            root_fd,
            "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json",
            "ROOT/runtime manifest",
            directory=False,
        )
        for name in SUPPORT_SHA256:
            support_fds[name] = open_child(root_fd, name, f"ROOT/support:{name}", directory=False)

        # Install the recursive guard before the first inventory.  Directory
        # watches cover the configured entry/mode masks; held regular-inode
        # watches cover the configured inode-write masks.  Fresh entry listings
        # and per-file digest/identity checks bracket setup, while full ROOT and
        # private inventories are recomputed at the end.  These independent
        # comparisons close setup/final-state gaps without claiming that every
        # possible inotify event is observable.
        guard = RecursiveInotifyGuard(root_fd)
        root_names = set(fresh_directory_names(root_fd, "final ROOT exact children"))
        if root_names != ROOT_CHILDREN:
            raise SmokeError("final ROOT exact child set mismatch")

        bundle_info = os.fstat(bundle_fd)
        if (
            bundle_info.st_dev != build["runtime"]["bundle_root_device"]
            or bundle_info.st_ino != build["runtime"]["bundle_root_inode"]
        ):
            raise SmokeError("held bundle identity mismatch")
        private_info = os.fstat(private_fd)
        if (
            private_info.st_dev != build["runtime"]["private_root_device"]
            or private_info.st_ino != build["runtime"]["private_root_inode"]
        ):
            raise SmokeError("held private runtime identity mismatch")

        bundle = verify_v10_bundle(bundle_fd)
        for name, expected_sha in SUPPORT_SHA256.items():
            expected = build["support_files"][name]
            support_info = os.fstat(support_fds[name])
            digest, size = digest_fd(support_fds[name], f"ROOT/support:{name}")
            if (
                support_info.st_dev != expected["device"]
                or support_info.st_ino != expected["inode"]
                or digest != expected["sha256"]
                or size != expected["size_bytes"]
                or digest != expected_sha
                or digest != bundle["member_sha256"].get(name)
            ):
                raise SmokeError(f"support/bundle exact byte binding mismatch: {name}")
        guard.assert_clean("bundle/support verification")

        manifest_raw = read_fd_bytes(
            manifest_fd, "runtime manifest", maximum_size=256 * 1024 * 1024
        )
        manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
        if manifest_sha != args.expected_runtime_manifest_sha256:
            raise SmokeError("held runtime manifest SHA mismatch")
        manifest_json = strict_json_bytes(manifest_raw, "runtime manifest")

        guard.assert_clean("before pre-import inventories")
        private_structural_before = inventory_tree(
            private_fd,
            "private runtime before imports",
            include_root=True,
            require_frozen_modes=True,
        )
        private_files_before = files_only_records(private_structural_before)
        private_structural_digest_before = structural_digest(private_structural_before)
        private_files_digest_before = files_only_digest(private_files_before)
        if (
            private_structural_digest_before
            != args.expected_structural_private_tree_digest
        ):
            raise SmokeError("private structural digest differs from authorization/build receipt")
        if private_files_digest_before != args.expected_files_only_private_root_digest:
            raise SmokeError("private files-only digest differs from authorization/build receipt")
        runtime = validate_runtime_manifest(
            manifest_json,
            expected_private_path=os.fspath(root_path / "private_runtime_site_packages"),
            actual_records=private_files_before,
            expected_root_digest=args.expected_files_only_runtime_root_digest,
        )

        root_structural_before = inventory_tree(
            root_fd,
            "full ROOT before imports",
            include_root=True,
            require_frozen_modes=True,
        )
        root_files_before = files_only_records(root_structural_before)
        root_structural_digest_before = structural_digest(root_structural_before)
        root_files_digest_before = files_only_digest(root_files_before)
        if root_structural_digest_before != args.expected_structural_full_root_digest:
            raise SmokeError("full ROOT structural digest differs from authorization/build receipt")
        if root_files_digest_before != args.expected_files_only_full_root_digest:
            raise SmokeError("full ROOT files-only digest differs from authorization/build receipt")

        scratch_structural_before = inventory_tree(
            scratch_fd,
            "scratch before imports",
            include_root=False,
            require_frozen_modes=False,
        )
        scratch_digest_before = structural_digest(scratch_structural_before)
        if (
            scratch_structural_before
            or scratch_digest_before != args.expected_empty_scratch_digest
        ):
            raise SmokeError("scratch must be precreated and exactly empty")
        guard.assert_clean("pre-import inventories")

        imported, module_evidence = import_and_verify(
            private_fd,
            runtime,
            scratch_fd,
            private_structural_before,
            guard,
        )

        scratch_structural_after = inventory_tree(
            scratch_fd,
            "scratch after imports",
            include_root=False,
            require_frozen_modes=False,
        )
        scratch_structural_after_repeat = inventory_tree(
            scratch_fd,
            "scratch stable recheck",
            include_root=False,
            require_frozen_modes=False,
        )
        if scratch_structural_after != scratch_structural_after_repeat:
            raise SmokeError("scratch inventory did not stabilize after imports")
        scratch_info_after = os.fstat(scratch_fd)
        if (
            scratch_info_after.st_dev != scratch_info.st_dev
            or scratch_info_after.st_ino != scratch_info.st_ino
            or stat.S_IMODE(scratch_info_after.st_mode) != 0o700
        ):
            raise SmokeError("scratch held identity/mode changed during imports")
        scratch_digest_after = structural_digest(scratch_structural_after)
        delta = scratch_delta(scratch_structural_before, scratch_structural_after)

        private_structural_after = inventory_tree(
            private_fd,
            "private runtime after imports",
            include_root=True,
            require_frozen_modes=True,
        )
        private_files_after = files_only_records(private_structural_after)
        private_structural_digest_after = structural_digest(private_structural_after)
        private_files_digest_after = files_only_digest(private_files_after)
        if (
            private_structural_after != private_structural_before
            or private_structural_digest_after != private_structural_digest_before
            or private_files_after != private_files_before
            or private_files_digest_after != private_files_digest_before
        ):
            raise SmokeError("private runtime tree changed during imports")
        if private_files_digest_after != args.expected_files_only_private_root_digest:
            raise SmokeError("private runtime files-only digest changed during imports")

        root_structural_after = inventory_tree(
            root_fd,
            "full ROOT after imports",
            include_root=True,
            require_frozen_modes=True,
        )
        root_files_after = files_only_records(root_structural_after)
        root_structural_digest_after = structural_digest(root_structural_after)
        root_files_digest_after = files_only_digest(root_files_after)
        if (
            root_structural_after != root_structural_before
            or root_structural_digest_after != root_structural_digest_before
            or root_files_after != root_files_before
            or root_files_digest_after != root_files_digest_before
        ):
            raise SmokeError("full ROOT tree changed during imports")
        guard.assert_clean("post-import inventories")

        if hashlib.sha256(read_fd_bytes(auth_fd, "smoke authorization final recheck", maximum_size=16 * 1024 * 1024)).hexdigest() != auth_sha:
            raise SmokeError("held smoke authorization bytes changed")
        if hashlib.sha256(read_fd_bytes(build_fd, "build PASS receipt final recheck", maximum_size=64 * 1024 * 1024)).hexdigest() != build_sha:
            raise SmokeError("held build PASS receipt bytes changed")
        if hashlib.sha256(read_fd_bytes(
            outer_launch_fd,
            "outer preflight launch receipt final recheck",
            maximum_size=64 * 1024 * 1024,
        )).hexdigest() != outer_launch_sha:
            raise SmokeError("held outer preflight launch receipt bytes changed")
        if hashlib.sha256(read_fd_bytes(manifest_fd, "runtime manifest final recheck", maximum_size=256 * 1024 * 1024)).hexdigest() != manifest_sha:
            raise SmokeError("held runtime manifest bytes changed")
        if digest_fd(script_fd, "smoke script final recheck")[0] != script_sha:
            raise SmokeError("held smoke script bytes changed")
        held_source_final = os.fstat(SMOKE_SOURCE_FD)
        if _stat_full_file_identity(held_source_final) != (
            bootstrap_context["smoke_source_identity"]
        ) or digest_fd(SMOKE_SOURCE_FD, "executed held source final recheck")[0] != (
            bootstrap_context["smoke_source_sha256"]
        ):
            raise SmokeError("executed held smoke source changed during smoke")
        if verify_v10_bundle(bundle_fd) != bundle:
            raise SmokeError("held bundle changed during smoke")
        for name, fd in support_fds.items():
            expected = build["support_files"][name]
            info = os.fstat(fd)
            digest, size = digest_fd(fd, f"support final recheck:{name}")
            if (
                info.st_dev != expected["device"]
                or info.st_ino != expected["inode"]
                or digest != expected["sha256"]
                or size != expected["size_bytes"]
            ):
                raise SmokeError(f"held support identity/bytes changed: {name}")

        _revalidate_named_child(root_fd, "bundle", bundle_fd, "bundle")
        _revalidate_named_child(root_fd, "private_runtime_site_packages", private_fd, "private runtime")
        _revalidate_named_child(root_fd, "RUNTIME_DEPENDENCY_IDENTITY_MANIFEST.json", manifest_fd, "runtime manifest")
        for name, fd in support_fds.items():
            _revalidate_named_child(root_fd, name, fd, f"support:{name}")
        if set(fresh_directory_names(root_fd, "final ROOT exact children recheck")) != ROOT_CHILDREN:
            raise SmokeError("final ROOT exact child set changed")
        current_root = os.stat(root_name, dir_fd=root_parent_fd, follow_symlinks=False)
        if (current_root.st_dev, current_root.st_ino) != (root_info.st_dev, root_info.st_ino):
            raise SmokeError("final ROOT path no longer names held ROOT inode")
        current_scratch = os.stat(scratch_name, dir_fd=scratch_parent_fd, follow_symlinks=False)
        if (current_scratch.st_dev, current_scratch.st_ino) != (scratch_info.st_dev, scratch_info.st_ino):
            raise SmokeError("scratch path no longer names held scratch inode")
        current_auth = os.stat(auth_name, dir_fd=auth_parent_fd, follow_symlinks=False)
        if (current_auth.st_dev, current_auth.st_ino) != (auth_info.st_dev, auth_info.st_ino):
            raise SmokeError("authorization path no longer names held authorization inode")
        current_build = os.stat(
            PurePosixPath(args.build_pass_receipt).name,
            dir_fd=build_parent_fd,
            follow_symlinks=False,
        )
        if (current_build.st_dev, current_build.st_ino) != (
            build_info.st_dev,
            build_info.st_ino,
        ):
            raise SmokeError("build receipt path no longer names held receipt inode")
        current_outer_launch = os.stat(
            outer_launch_name,
            dir_fd=outer_launch_parent_fd,
            follow_symlinks=False,
        )
        if (current_outer_launch.st_dev, current_outer_launch.st_ino) != (
            outer_launch_info.st_dev,
            outer_launch_info.st_ino,
        ):
            raise SmokeError(
                "outer preflight launch receipt path no longer names held inode"
            )
        current_lock = os.stat(
            lock_name, dir_fd=lock_parent_fd, follow_symlinks=False
        )
        if (current_lock.st_dev, current_lock.st_ino) != (
            lock_info.st_dev,
            lock_info.st_ino,
        ):
            raise SmokeError("journal lock path no longer names held lock inode")
        _revalidate_named_child(journal_fd, "LOCK", lock_fd, "journal lock final")
        current_journal = os.stat(
            journal_name, dir_fd=journal_parent_fd, follow_symlinks=False
        )
        if (current_journal.st_dev, current_journal.st_ino) != (
            journal_info.st_dev,
            journal_info.st_ino,
        ):
            raise SmokeError("journal directory path no longer names held journal inode")
        current_script = os.stat(
            PurePosixPath(auth["paths"]["smoke_script"]).name,
            dir_fd=script_parent_fd,
            follow_symlinks=False,
        )
        script_info = os.fstat(script_fd)
        if (current_script.st_dev, current_script.st_ino) != (
            script_info.st_dev,
            script_info.st_ino,
        ):
            raise SmokeError("smoke script path no longer names held script inode")
        guard.assert_clean("final held identity rechecks")

        inotify_watch_records = sorted(
            (dict(entry) for entry in guard.watch_table.values()),
            key=lambda item: (
                item["relative_path"], item["kind"], item["device"], item["inode"]
            ),
        )
        inotify_watch_table_sha256 = hashlib.sha256(
            json.dumps(
                inotify_watch_records,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

        output = {
            "schema": "historical_200k_fixed10k_result_free_runtime_layout_smoke_v10",
            "status": "PASS_EXACT_RESULT_FREE_RUNTIME_LAYOUT_SMOKE_V10_ONLY",
            "authorization": {
                "decision_id": auth["decision_id"],
                "sha256": auth_sha,
                "single_nofollow_open_same_bytes_hashed_and_parsed": True,
                "exact_process_argv_template_matched": True,
                "self_sha_placeholder_rule": AUTH_SHA_PLACEHOLDER,
            },
            "smoke_source_execution": {
                "execution_origin": f"/proc/self/fd/{SMOKE_SOURCE_FD}",
                "execution_method": (
                    "FROZEN_DASH_C_BOOTSTRAP_PREAD_STABLE_FSTAT_SHA256_"
                    "COMPILE_EXEC_HELD_BYTES_V1"
                ),
                "original_evidence_path": auth["paths"]["smoke_script"],
                "original_evidence_path_was_not_executed": True,
                "held_source_device": held_script_info.st_dev,
                "held_source_inode": held_script_info.st_ino,
                "held_source_size_bytes": held_script_info.st_size,
                "held_source_sha256": bootstrap_context["smoke_source_sha256"],
                "bootstrap_sha256": HELD_SMOKE_BOOTSTRAP_SHA256,
                "interpreter_execution_origin": f"/proc/self/fd/{INTERPRETER_FD}",
                "interpreter_device": bootstrap_context["interpreter_identity"]["device"],
                "interpreter_inode": bootstrap_context["interpreter_identity"]["inode"],
                "interpreter_sha256": bootstrap_context["interpreter_sha256"],
                "same_held_authorization_fd_hashed_before_compile_and_parsed": True,
            },
            "build_pass_receipt": {
                "decision_id": build["decision_id"],
                "sha256": build_sha,
                "status": BUILD_RECEIPT_STATUS,
                "journal_lock": {
                    "path": build["journal"]["lock_path"],
                    "device": lock_info.st_dev,
                    "inode": lock_info.st_ino,
                    "mode": "0600",
                    "single_link_regular_nofollow_held_and_revalidated": True,
                },
                "journal_directory": {
                    "path": build["journal"]["directory"],
                    "device": journal_info.st_dev,
                    "inode": journal_info.st_ino,
                    "mode": "0700",
                    "nofollow_held_and_revalidated": True,
                },
                "trusted_outer_launch": {
                    "path": build["trusted_launch"]["outer_launch_receipt_path"],
                    "sha256": outer_launch_sha,
                    "device": outer_launch_info.st_dev,
                    "inode": outer_launch_info.st_ino,
                    "mode": "0444",
                    "single_nofollow_open_same_bytes_hashed_held_and_revalidated": True,
                },
            },
            "final_root": {
                "path": args.final_root,
                "device": root_info.st_dev,
                "inode": root_info.st_ino,
                "files_only_digest_algorithm": FILES_ONLY_DIGEST_ALGORITHM,
                "files_only_record_count": len(root_files_before),
                "files_only_digest_before": root_files_digest_before,
                "files_only_digest_after": root_files_digest_after,
                "structural_digest_algorithm": STRUCTURAL_DIGEST_ALGORITHM,
                "structural_record_count": len(root_structural_before),
                "structural_digest_before": root_structural_digest_before,
                "structural_digest_after": root_structural_digest_after,
                "unchanged": True,
            },
            "bundle": {
                **{key: value for key, value in bundle.items() if key != "member_sha256"},
                "path": build["runtime"]["bundle_root_path"],
                "device": bundle_info.st_dev,
                "inode": bundle_info.st_ino,
            },
            "support_files": build["support_files"],
            "runtime": {
                "manifest_sha256": manifest_sha,
                "files_only_digest_algorithm": FILES_ONLY_DIGEST_ALGORITHM,
                "files_only_root_digest": runtime["files_only_root_digest"],
                "files_only_record_count": len(private_files_before),
                "files_only_digest_before": private_files_digest_before,
                "files_only_digest_after": private_files_digest_after,
                "structural_digest_algorithm": STRUCTURAL_DIGEST_ALGORITHM,
                "structural_record_count": len(private_structural_before),
                "structural_digest_before": private_structural_digest_before,
                "structural_digest_after": private_structural_digest_after,
                "unchanged": True,
            },
            "imports": imported,
            "module_origin_evidence": module_evidence,
            "scratch": {
                "path": args.scratch_dir,
                "device": scratch_info.st_dev,
                "inode": scratch_info.st_ino,
                "mode": "0700",
                "inventory_digest_algorithm": SCRATCH_INVENTORY_DIGEST_ALGORITHM,
                "inventory_before": scratch_structural_before,
                "inventory_digest_before": scratch_digest_before,
                "inventory_after": scratch_structural_after,
                "inventory_digest_after": scratch_digest_after,
                "delta": delta,
                "known_import_write_roots_bound_to_held_scratch_fd": list(ENVIRONMENT_KEYS),
                "global_no_write_claim_made": False,
            },
            "isolation": {
                "flags": ["-I", "-B", "-S"],
                "private_runtime_path": f"/proc/self/fd/{private_fd}",
                "scratch_path": f"/proc/self/fd/{scratch_fd}",
                "recursive_inotify_watch_count": len(guard.watch_paths),
                "recursive_inotify_directory_watch_count": sum(
                    entry["kind"] == "directory"
                    for entry in inotify_watch_records
                ),
                "recursive_inotify_regular_inode_watch_count": sum(
                    entry["kind"] == "regular"
                    for entry in inotify_watch_records
                ),
                "recursive_inotify_watch_table_sha256": (
                    inotify_watch_table_sha256
                ),
                "recursive_inotify_watch_records": inotify_watch_records,
                "inotify_failure_mask": INOTIFY_FAILURE_MASK,
                "inotify_directory_watch_mask": INOTIFY_DIRECTORY_WATCH_MASK,
                "inotify_regular_file_watch_mask": INOTIFY_REGULAR_FILE_WATCH_MASK,
                "watch_setup_bracketed_by_entry_digest_identity_checks": True,
                "root_private_pre_post_inventory_revalidation": True,
                "all_inotify_events_or_all_filesystem_mutations_claimed": False,
                "concurrent_mutation_detection_only": True,
            },
            "scope": {
                "root_or_bundle_files_written": False,
                "scratch_delta_reported_without_global_no_write_claim": True,
                "entrypoint_or_pipeline_execution_performed": False,
                "result_or_metric_access_performed": False,
                "signal_or_process_control_performed": False,
                "deployment_or_resume_performed": False,
                "network_access_performed": False,
                "global_immutability_or_no_side_effect_claim_made": False,
            },
        }
        print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))
        return 0
    finally:
        if guard is not None:
            guard.close()
        for fd in support_fds.values():
            if fd >= 0:
                os.close(fd)
        for fd in (
            manifest_fd,
            private_fd,
            bundle_fd,
            scratch_fd,
            scratch_parent_fd,
            root_fd,
            root_parent_fd,
            script_fd,
            script_parent_fd,
            build_fd,
            build_parent_fd,
            outer_launch_fd,
            outer_launch_parent_fd,
            lock_fd,
            lock_parent_fd,
            journal_fd,
            journal_parent_fd,
            auth_fd,
            auth_parent_fd,
        ):
            if fd >= 0:
                os.close(fd)


def held_byte_bootstrap_main(
    smoke_argv: Sequence[str], bootstrap_context: Mapping[str, Any]
) -> int:
    """Only authenticated execution entry; called by the frozen ``-c`` text."""

    args = _parse_cli(smoke_argv)
    _require_platform_fd_features()
    if not (
        sys.flags.isolated == 1
        and sys.flags.no_site == 1
        and sys.flags.dont_write_bytecode == 1
    ):
        raise SmokeError("exact -I -B -S interpreter flags are required")
    validated_context = _validate_held_bootstrap_context(
        smoke_argv, bootstrap_context, args
    )
    return _authenticated_smoke_main(
        args,
        validated_context["actual_cmdline"],
        smoke_argv,
        validated_context,
    )


def main() -> int:
    """Reject pathname/module callers before the authenticated smoke body."""

    raise SmokeError(
        "direct smoke main is forbidden; use the frozen FD197/FD198 held-byte bootstrap"
    )


if __name__ == "__main__":
    print(
        "FAIL_CLOSED: direct pathname smoke execution is forbidden; "
        "use the frozen FD197/FD198 held-byte bootstrap",
        file=sys.stderr,
    )
    raise SystemExit(2)
