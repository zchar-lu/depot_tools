# coding=utf-8
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Collection of subprocess wrapper functions.

In theory you shouldn't need anything else in subprocess, or this module failed.
"""

import codecs
import errno
import logging
import os
import subprocess
import sys
import threading

# Cache the string-escape codec to ensure subprocess can find it later.
# See crbug.com/912292#c2 for context.
if sys.version_info.major == 2:
    codecs.lookup("string-escape")
    # Sends stdout or stderr to os.devnull.
    DEVNULL = open(os.devnull, "r+")
else:
    # pylint: disable=redefined-builtin
    basestring = (str, bytes)
    DEVNULL = subprocess.DEVNULL


# Constants forwarded from subprocess.
PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT


class CalledProcessError(subprocess.CalledProcessError):
    """Augment the standard exception with more data."""

    def __init__(self, returncode, cmd, cwd, stdout, stderr):
        super(CalledProcessError, self).__init__(returncode, cmd, output=stdout)
        self.stdout = self.output  # for backward compatibility.
        self.stderr = stderr
        self.cwd = cwd

    def __str__(self):
        out = "Command %r returned non-zero exit status %s" % (
            " ".join(self.cmd),
            self.returncode,
        )
        if self.cwd:
            out += " in " + self.cwd
        if self.stdout:
            out += "\n" + self.stdout.decode("utf-8", "ignore")
        if self.stderr:
            out += "\n" + self.stderr.decode("utf-8", "ignore")
        return out


class CygwinRebaseError(CalledProcessError):
    """Occurs when cygwin's fork() emulation fails due to rebased dll."""


## Utility functions


def kill_pid(pid):
    """Kills a process by its process id."""
    try:
        # Unable to import 'module'
        # pylint: disable=no-member,F0401
        import signal

        return os.kill(pid, signal.SIGTERM)
    except ImportError:
        pass


def get_english_env(env):
    """Forces LANG and/or LANGUAGE to be English.

    Forces encoding to utf-8 for subprocesses.

    Returns None if it is unnecessary.
    """
    if sys.platform == "win32":
        return None
    env = env or os.environ

    # Test if it is necessary at all.
    is_english = lambda name: env.get(name, "en").startswith("en")

    if is_english("LANG") and is_english("LANGUAGE"):
        return None

    # Requires modifications.
    env = env.copy()

    def fix_lang(name):
        if not is_english(name):
            env[name] = "en_US.UTF-8"

    fix_lang("LANG")
    fix_lang("LANGUAGE")
    return env


class Popen(subprocess.Popen):
    """Wraps subprocess.Popen() with various workarounds.

    - Forces English output since it's easier to parse the stdout if it is always
      in English.
    - Sets shell=True on windows by default. You can override this by forcing
      shell parameter to a value.
    - Adds support for DEVNULL to not buffer when not needed.
    - Adds self.start property.

    Note: Popen() can throw OSError when cwd or args[0] doesn't exist. Translate
    exceptions generated by cygwin when it fails trying to emulate fork().
    """

    # subprocess.Popen.__init__() is not threadsafe; there is a race between
    # creating the exec-error pipe for the child and setting it to CLOEXEC during
    # which another thread can fork and cause the pipe to be inherited by its
    # descendents, which will cause the current Popen to hang until all those
    # descendents exit. Protect this with a lock so that only one fork/exec can
    # happen at a time.
    popen_lock = threading.Lock()

    def __init__(self, args, **kwargs):
        env = get_english_env(kwargs.get("env"))
        if env:
            kwargs["env"] = env
        if kwargs.get("env") is not None and sys.version_info.major != 2:
            # Subprocess expects environment variables to be strings in Python 3.
            def ensure_str(value):
                if isinstance(value, bytes):
                    return value.decode()
                return value

            kwargs["env"] = {
                ensure_str(k): ensure_str(v) for k, v in kwargs["env"].items()
            }
        if kwargs.get("shell") is None:
            # *Sigh*:  Windows needs shell=True, or else it won't search %PATH% for
            # the executable, but shell=True makes subprocess on Linux fail when it's
            # called with a list because it only tries to execute the first item in
            # the list.
            kwargs["shell"] = bool(sys.platform == "win32")

        if isinstance(args, basestring):
            tmp_str = args
        elif isinstance(args, (list, tuple)):
            tmp_str = " ".join(args)
        else:
            raise CalledProcessError(None, args, kwargs.get("cwd"), None, None)
        if kwargs.get("cwd", None):
            tmp_str += ";  cwd=%s" % kwargs["cwd"]
        logging.debug(tmp_str)

        try:
            with self.popen_lock:
                super(Popen, self).__init__(args, **kwargs)
        except OSError as e:
            if e.errno == errno.EAGAIN and sys.platform == "cygwin":
                # Convert fork() emulation failure into a CygwinRebaseError().
                raise CygwinRebaseError(
                    e.errno,
                    args,
                    kwargs.get("cwd"),
                    None,
                    "Visit "
                    "http://code.google.com/p/chromium/wiki/CygwinDllRemappingFailure "
                    "to learn how to fix this error; you need to rebase your cygwin "
                    "dlls",
                )
            # Popen() can throw OSError when cwd or args[0] doesn't exist.
            raise OSError(
                "Execution failed with error: %s.\n"
                "Check that %s or %s exist and have execution permission."
                % (str(e), kwargs.get("cwd"), args[0])
            )


def communicate(args, **kwargs):
    """Wraps subprocess.Popen().communicate().

    Returns ((stdout, stderr), returncode).

    - If the subprocess runs for |nag_timer| seconds without producing terminal
      output, print a warning to stderr.
    - Automatically passes stdin content as input so do not specify stdin=PIPE.
    """
    stdin = None
    # When stdin is passed as an argument, use it as the actual input data and
    # set the Popen() parameter accordingly.
    if "stdin" in kwargs and isinstance(kwargs["stdin"], basestring):
        stdin = kwargs["stdin"]
        kwargs["stdin"] = PIPE

    proc = Popen(args, **kwargs)
    return proc.communicate(stdin), proc.returncode


def call(args, **kwargs):
    """Emulates subprocess.call().

    Automatically convert stdout=PIPE or stderr=PIPE to DEVNULL.
    In no case they can be returned since no code path raises
    subprocess2.CalledProcessError.

    Returns exit code.
    """
    if kwargs.get("stdout") == PIPE:
        kwargs["stdout"] = DEVNULL
    if kwargs.get("stderr") == PIPE:
        kwargs["stderr"] = DEVNULL
    return communicate(args, **kwargs)[1]


def check_call_out(args, **kwargs):
    """Improved version of subprocess.check_call().

    Returns (stdout, stderr), unlike subprocess.check_call().
    """
    out, returncode = communicate(args, **kwargs)
    if returncode:
        raise CalledProcessError(
            returncode, args, kwargs.get("cwd"), out[0], out[1]
        )
    return out


def check_call(args, **kwargs):
    """Emulate subprocess.check_call()."""
    check_call_out(args, **kwargs)
    return 0


def capture(args, **kwargs):
    """Captures stdout of a process call and returns it.

    Returns stdout.

    - Discards returncode.
    - Blocks stdin by default if not specified since no output will be visible.
    """
    kwargs.setdefault("stdin", DEVNULL)

    # Like check_output, deny the caller from using stdout arg.
    return communicate(args, stdout=PIPE, **kwargs)[0][0]


def check_output(args, **kwargs):
    """Emulates subprocess.check_output().

    Captures stdout of a process call and returns stdout only.

    - Throws if return code is not 0.
    - Works even prior to python 2.7.
    - Blocks stdin by default if not specified since no output will be visible.
    - As per doc, "The stdout argument is not allowed as it is used internally."
    """
    kwargs.setdefault("stdin", DEVNULL)
    if "stdout" in kwargs:
        raise ValueError("stdout argument not allowed, it would be overridden.")
    return check_call_out(args, stdout=PIPE, **kwargs)[0]
