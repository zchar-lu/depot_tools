#!/usr/bin/env vpython3
# coding=utf-8
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for git_cl.py."""

import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import git_auth


class TestGitAuthConfigChanger(unittest.TestCase):

    def setUp(self):
        self.git = FAKE_GIT()

    def test_apply_new_auth(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        want = {
            '/some/fake/dir': {
                'credential.https://chromium.googlesource.com/.helper':
                ['', 'luci'],
                'http.cookieFile': [''],
            },
        }
        self.assertEqual(self.git.config, want)

    def test_apply_new_auth_sso(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH_SSO,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        want = {
            '/some/fake/dir': {
                'protocol.sso.allow': ['always'],
                'url.sso://chromium/.insteadOf':
                ['https://chromium.googlesource.com/'],
                'http.cookieFile': [''],
            },
        }
        self.assertEqual(self.git.config, want)

    def test_apply_no_auth(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NO_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        want = {
            '/some/fake/dir': {},
        }
        self.assertEqual(self.git.config, want)

    def test_apply_chain_sso_new(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH_SSO,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        want = {
            '/some/fake/dir': {
                'credential.https://chromium.googlesource.com/.helper':
                ['', 'luci'],
                'http.cookieFile': [''],
            },
        }
        self.assertEqual(self.git.config, want)

    def test_apply_chain_new_sso(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH_SSO,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        want = {
            '/some/fake/dir': {
                'protocol.sso.allow': ['always'],
                'url.sso://chromium/.insteadOf':
                ['https://chromium.googlesource.com/'],
                'http.cookieFile': [''],
            },
        }
        self.assertEqual(self.git.config, want)

    def test_apply_chain_new_no(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NO_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        want = {
            '/some/fake/dir': {},
        }
        self.assertEqual(self.git.config, want)

    def test_apply_chain_sso_no(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH_SSO,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NO_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply('/some/fake/dir')
        want = {
            '/some/fake/dir': {},
        }
        self.assertEqual(self.git.config, want)

    def test_apply_global_new_auth(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply_global('/some/fake/dir')
        want = {
            '<global>': {
                'credential.https://chromium.googlesource.com/.helper':
                ['', 'luci'],
            },
        }
        self.assertEqual(self.git.config, want)

    def test_apply_global_new_auth_sso(self):
        git_auth.GitAuthConfigChanger(
            mode=git_auth.GitAuthMode.NEW_AUTH_SSO,
            remote_url=
            'https://chromium.googlesource.com/chromium/tools/depot_tools.git',
            set_config_func=self.git.SetConfig,
        ).apply_global('/some/fake/dir')
        want = {
            '<global>': {
                'protocol.sso.allow': ['always'],
                'url.sso://chromium/.insteadOf':
                ['https://chromium.googlesource.com/'],
            },
        }
        self.assertEqual(self.git.config, want)


class FAKE_GIT(object):
    """Fake implementation of GIT for testing.

    Note that this uses instance methods and not static methods, to
    isolate test state.
    """

    def __init__(self):
        self.config: Mapping[str, Mapping[str, List[str]]] = {}

    def SetConfig(
        self,
        cwd,
        key,
        value=None,
        *,
        append=False,
        missing_ok=True,
        modify_all=False,
        scope='local',
        value_pattern=None,
    ):
        if scope not in ('local', 'global'):
            raise NotImplementedError(f"FAKE_GIT does not implement {scope=}")
        if value_pattern is not None:
            raise NotImplementedError(
                "FAKE_GIT does not implement value_pattern")
        if scope == 'global':
            cwd = '<global>'
        cfg = self.config.setdefault(cwd, {})
        values = cfg.setdefault(key, [])
        if value is None:
            if (len(values) == 1) or modify_all:
                del cfg[key]
                return
            if len(values) == 0:
                if missing_ok:
                    del cfg[key]
                    return
                raise Exception(f'{key=} is missing')
            raise Exception(f'{key=} has multiple values {values=}')
        if append:
            values.append(value)
            return
        if len(values) > 1 and not modify_all:
            raise Exception(f'{key=} has multiple values {values=}')
        values[:] = [value]


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG if '-v' in sys.argv else logging.ERROR)
    unittest.main()