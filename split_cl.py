#!/usr/bin/env python3
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Splits a branch into smaller branches and uploads CLs."""

import collections
import os
import re
import subprocess2
import sys

import gclient_utils
import git_footers
import scm

import git_common as git

# If a call to `git cl split` will generate more than this number of CLs, the
# command will prompt the user to make sure they know what they're doing. Large
# numbers of CLs generated by `git cl split` have caused infrastructure issues
# in the past.
CL_SPLIT_FORCE_LIMIT = 10

# The maximum number of top reviewers to list. `git cl split` may send many CLs
# to a single reviewer, so the top reviewers with the most CLs sent to them
# will be listed.
CL_SPLIT_TOP_REVIEWERS = 5

FilesAndOwnersDirectory = collections.namedtuple("FilesAndOwnersDirectory",
                                                 "files owners_directories")


def EnsureInGitRepository():
    """Throws an exception if the current directory is not a git repository."""
    git.run('rev-parse')


def CreateBranchForDirectories(prefix, directories, upstream):
    """Creates a branch named |prefix| + "_" + |directories[0]| + "_split".

    Return false if the branch already exists. |upstream| is used as upstream
    for the created branch.
    """
    existing_branches = set(git.branches(use_limit=False))
    branch_name = prefix + '_' + directories[0] + '_split'
    if branch_name in existing_branches:
        return False
    git.run('checkout', '-t', upstream, '-b', branch_name)
    return True


def FormatDirectoriesForPrinting(directories, prefix=None):
    """Formats directory list for printing

    Uses dedicated format for single-item list."""

    prefixed = directories
    if prefix:
        prefixed = [(prefix + d) for d in directories]

    return str(prefixed) if len(prefixed) > 1 else str(prefixed[0])


def FormatDescriptionOrComment(txt, directories):
    """Replaces $directory with |directories| in |txt|."""
    to_insert = FormatDirectoriesForPrinting(directories)
    return txt.replace('$directory', to_insert)


def AddUploadedByGitClSplitToDescription(description):
    """Adds a 'This CL was uploaded by git cl split.' line to |description|.

    The line is added before footers, or at the end of |description| if it has
    no footers.
    """
    split_footers = git_footers.split_footers(description)
    lines = split_footers[0]
    if lines[-1] and not lines[-1].isspace():
        lines = lines + ['']
    lines = lines + ['This CL was uploaded by git cl split.']
    if split_footers[1]:
        lines += [''] + split_footers[1]
    return '\n'.join(lines)


def UploadCl(refactor_branch, refactor_branch_upstream, directories, files,
             description, comment, reviewers, changelist, cmd_upload,
             cq_dry_run, enable_auto_submit, topic, repository_root):
    """Uploads a CL with all changes to |files| in |refactor_branch|.

    Args:
        refactor_branch: Name of the branch that contains the changes to upload.
        refactor_branch_upstream: Name of the upstream of |refactor_branch|.
        directories: Paths to the directories that contain the OWNERS files for
            which to upload a CL.
        files: List of AffectedFile instances to include in the uploaded CL.
        description: Description of the uploaded CL.
        comment: Comment to post on the uploaded CL.
        reviewers: A set of reviewers for the CL.
        changelist: The Changelist class.
        cmd_upload: The function associated with the git cl upload command.
        cq_dry_run: If CL uploads should also do a cq dry run.
        enable_auto_submit: If CL uploads should also enable auto submit.
        topic: Topic to associate with uploaded CLs.
    """
    # Create a branch.
    if not CreateBranchForDirectories(refactor_branch, directories,
                                      refactor_branch_upstream):
        print('Skipping ' + FormatDirectoriesForPrinting(directories) +
              ' for which a branch already exists.')
        return

    # Checkout all changes to files in |files|.
    deleted_files = []
    modified_files = []
    for action, f in files:
        abspath = os.path.abspath(os.path.join(repository_root, f))
        if action == 'D':
            deleted_files.append(abspath)
        else:
            modified_files.append(abspath)

    if deleted_files:
        git.run(*['rm'] + deleted_files)
    if modified_files:
        git.run(*['checkout', refactor_branch, '--'] + modified_files)

    # Commit changes. The temporary file is created with delete=False so that it
    # can be deleted manually after git has read it rather than automatically
    # when it is closed.
    with gclient_utils.temporary_file() as tmp_file:
        gclient_utils.FileWrite(
            tmp_file, FormatDescriptionOrComment(description, directories))
        git.run('commit', '-F', tmp_file)

    # Upload a CL.
    upload_args = ['-f']
    if reviewers:
        upload_args.extend(['-r', ','.join(sorted(reviewers))])
    if cq_dry_run:
        upload_args.append('--cq-dry-run')
    if not comment:
        upload_args.append('--send-mail')
    if enable_auto_submit:
        upload_args.append('--enable-auto-submit')
    if topic:
        upload_args.append('--topic={}'.format(topic))
    print('Uploading CL for ' + FormatDirectoriesForPrinting(directories) +
          '...')

    ret = cmd_upload(upload_args)
    if ret != 0:
        print('Uploading failed.')
        print('Note: git cl split has built-in resume capabilities.')
        print('Delete ' + git.current_branch() +
              ' then run git cl split again to resume uploading.')

    if comment:
        changelist().AddComment(FormatDescriptionOrComment(
            comment, directories),
                                publish=True)


def GetFilesSplitByOwners(files, max_depth):
    """Returns a map of files split by OWNERS file.

    Returns:
        A map where keys are paths to directories containing an OWNERS file and
        values are lists of files sharing an OWNERS file.
    """
    files_split_by_owners = {}
    for action, path in files:
        # normpath() is important to normalize separators here, in prepration
        # for str.split() before. It would be nicer to use something like
        # pathlib here but alas...
        dir_with_owners = os.path.normpath(os.path.dirname(path))
        if max_depth >= 1:
            dir_with_owners = os.path.join(
                *dir_with_owners.split(os.path.sep)[:max_depth])
        # Find the closest parent directory with an OWNERS file.
        while (dir_with_owners not in files_split_by_owners
               and not os.path.isfile(os.path.join(dir_with_owners, 'OWNERS'))):
            dir_with_owners = os.path.dirname(dir_with_owners)
        files_split_by_owners.setdefault(dir_with_owners, []).append(
            (action, path))
    return files_split_by_owners


def PrintClInfo(cl_index, num_cls, directories, file_paths, description,
                reviewers, cq_dry_run, enable_auto_submit, topic):
    """Prints info about a CL.

    Args:
        cl_index: The index of this CL in the list of CLs to upload.
        num_cls: The total number of CLs that will be uploaded.
        directories: Paths to directories that contains the OWNERS files for
            which to upload a CL.
        file_paths: A list of files in this CL.
        description: The CL description.
        reviewers: A set of reviewers for this CL.
        cq_dry_run: If the CL should also be sent to CQ dry run.
        enable_auto_submit: If the CL should also have auto submit enabled.
        topic: Topic to set for this CL.
    """
    description_lines = FormatDescriptionOrComment(description,
                                                   directories).splitlines()
    indented_description = '\n'.join(['    ' + l for l in description_lines])

    print('CL {}/{}'.format(cl_index, num_cls))
    print('Paths: {}'.format(FormatDirectoriesForPrinting(directories)))
    print('Reviewers: {}'.format(', '.join(reviewers)))
    print('Auto-Submit: {}'.format(enable_auto_submit))
    print('CQ Dry Run: {}'.format(cq_dry_run))
    print('Topic: {}'.format(topic))
    print('\n' + indented_description + '\n')
    print('\n'.join(file_paths))
    print()


def SplitCl(description_file, comment_file, changelist, cmd_upload, dry_run,
            cq_dry_run, enable_auto_submit, max_depth, topic, repository_root):
    """"Splits a branch into smaller branches and uploads CLs.

    Args:
        description_file: File containing the description of uploaded CLs.
        comment_file: File containing the comment of uploaded CLs.
        changelist: The Changelist class.
        cmd_upload: The function associated with the git cl upload command.
        dry_run: Whether this is a dry run (no branches or CLs created).
        cq_dry_run: If CL uploads should also do a cq dry run.
        enable_auto_submit: If CL uploads should also enable auto submit.
        max_depth: The maximum directory depth to search for OWNERS files. A
            value less than 1 means no limit.
        topic: Topic to associate with split CLs.

    Returns:
        0 in case of success. 1 in case of error.
    """
    description = AddUploadedByGitClSplitToDescription(
        gclient_utils.FileRead(description_file))
    comment = gclient_utils.FileRead(comment_file) if comment_file else None

    try:
        EnsureInGitRepository()

        cl = changelist()
        upstream = cl.GetCommonAncestorWithUpstream()
        files = [
            (action.strip(), f)
            for action, f in scm.GIT.CaptureStatus(repository_root, upstream)
        ]

        if not files:
            print('Cannot split an empty CL.')
            return 1

        author = git.run('config', 'user.email').strip() or None
        refactor_branch = git.current_branch()
        assert refactor_branch, "Can't run from detached branch."
        refactor_branch_upstream = git.upstream(refactor_branch)
        assert refactor_branch_upstream, \
            "Branch %s must have an upstream." % refactor_branch

        if not CheckDescriptionBugLink(description):
            return 0

        files_split_by_reviewers = SelectReviewersForFiles(
            cl, author, files, max_depth)

        num_cls = len(files_split_by_reviewers)
        print('Will split current branch (' + refactor_branch + ') into ' +
              str(num_cls) + ' CLs.\n')
        if not dry_run and num_cls > CL_SPLIT_FORCE_LIMIT:
            print(
                'This will generate "%r" CLs. This many CLs can potentially'
                ' generate too much load on the build infrastructure.\n\n'
                'Please email infra-dev@chromium.org to ensure that this won\'t'
                ' break anything. The infra team reserves the right to cancel'
                ' your jobs if they are overloading the CQ.\n\n'
                '(Alternatively, you can reduce the number of CLs created by'
                ' using the --max-depth option. Pass --dry-run to examine the'
                ' CLs which will be created until you are happy with the'
                ' results.)' % num_cls)
            answer = gclient_utils.AskForData('Proceed? (y/n):')
            if answer.lower() != 'y':
                return 0

        cls_per_reviewer = collections.defaultdict(int)
        for cl_index, (reviewers, cl_info) in \
            enumerate(files_split_by_reviewers.items(), 1):
            # Convert reviewers from tuple to set.
            reviewer_set = set(reviewers)
            if dry_run:
                file_paths = [f for _, f in cl_info.files]
                PrintClInfo(cl_index, num_cls, cl_info.owners_directories,
                            file_paths, description, reviewer_set, cq_dry_run,
                            enable_auto_submit, topic)
            else:
                UploadCl(refactor_branch, refactor_branch_upstream,
                         cl_info.owners_directories, cl_info.files, description,
                         comment, reviewer_set, changelist, cmd_upload,
                         cq_dry_run, enable_auto_submit, topic, repository_root)

            for reviewer in reviewers:
                cls_per_reviewer[reviewer] += 1

        # List the top reviewers that will be sent the most CLs as a result of
        # the split.
        reviewer_rankings = sorted(cls_per_reviewer.items(),
                                   key=lambda item: item[1],
                                   reverse=True)
        print('The top reviewers are:')
        for reviewer, count in reviewer_rankings[:CL_SPLIT_TOP_REVIEWERS]:
            print(f'    {reviewer}: {count} CLs')

        # Go back to the original branch.
        git.run('checkout', refactor_branch)

    except subprocess2.CalledProcessError as cpe:
        sys.stderr.write(cpe.stderr)
        return 1
    return 0


def CheckDescriptionBugLink(description):
    """Verifies that the description contains a bug link.

    Examples:
        Bug: 123
        Bug: chromium:456

    Prompts user if the description does not contain a bug link.
    """
    bug_pattern = re.compile(r"^Bug:\s*(?:[a-zA-Z]+:)?[0-9]+", re.MULTILINE)
    matches = re.findall(bug_pattern, description)
    answer = 'y'
    if not matches:
        answer = gclient_utils.AskForData(
            'Description does not include a bug link. Proceed? (y/n):')
    return answer.lower() == 'y'


def SelectReviewersForFiles(cl, author, files, max_depth):
    """Selects reviewers for passed-in files

    Args:
        cl: Changelist class instance
        author: Email of person running 'git cl split'
        files: List of files
        max_depth: The maximum directory depth to search for OWNERS files.
            A value less than 1 means no limit.
    """
    info_split_by_owners = GetFilesSplitByOwners(files, max_depth)

    info_split_by_reviewers = {}

    for (directory, split_files) in info_split_by_owners.items():
        # Use '/' as a path separator in the branch name and the CL description
        # and comment.
        directory = directory.replace(os.path.sep, '/')
        file_paths = [f for _, f in split_files]
        # Convert reviewers list to tuple in order to use reviewers as key to
        # dictionary.
        reviewers = tuple(
            cl.owners_client.SuggestOwners(
                file_paths, exclude=[author, cl.owners_client.EVERYONE]))

        if not reviewers in info_split_by_reviewers:
            info_split_by_reviewers[reviewers] = FilesAndOwnersDirectory([], [])
        info_split_by_reviewers[reviewers].files.extend(split_files)
        info_split_by_reviewers[reviewers].owners_directories.append(directory)

    return info_split_by_reviewers
