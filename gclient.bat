@echo off
:: Copyright (c) 2012 The Chromium Authors. All rights reserved.
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.
setlocal

:: Synchronize the root directory before deferring control back to gclient.py.
call "%~dp0update_depot_tools.bat" %*

:: Defer control.
"%~dp0python" "%~dp0gclient.py" %*
