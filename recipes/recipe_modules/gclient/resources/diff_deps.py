#!/usr/bin/env python
import os
import subprocess
import sys

if '@' in os.environ.get('GCLIENT_URL'):
  ref = os.environ.get('GCLIENT_URL').split('@')[-1]
else:
  sys.exit(0)

try:
  diff = subprocess.check_output("git diff --cached --name-only %s" % ref, shell=True)
except subprocess.CalledProcessError:
  sys.exit(0)

dep_path = os.environ.get('GCLIENT_DEP_PATH', '')
for line in diff.splitlines():
  if line:
    print(os.path.join(dep_path,  line))
