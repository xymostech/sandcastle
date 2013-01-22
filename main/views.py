from urllib2 import urlopen, HTTPError
from contextlib import closing
import errno
import json
import os
import re
import subprocess
import mimetypes
import shutil
import fcntl

from django.shortcuts import render_to_response, redirect
from django.template import RequestContext
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.utils import html, encoding
from django.conf import settings
import pygments
import pygments.lexers
import pygments.formatters

from models import PhabricatorReview

media_dir = os.path.join(settings.PROJECT_DIR, "media")


def make_base_dir(local=True, static_dir=""):
    if local:
        return os.path.join(media_dir, "repo")
    else:
        return os.path.join(media_dir, "castles", static_dir)


def make_git_dir(local=True, static_dir=""):
    return os.path.join(make_base_dir(local, static_dir), ".git")


def call_git(command, local=True, static_dir="", method=subprocess.call):
    return method(
        ["git", "--git-dir", make_git_dir(local, static_dir),
            "--work-tree", make_base_dir(local, static_dir)] + command)


def check_call_git(command, local=True, static_dir=""):
    return call_git(command, local=local, static_dir=static_dir,
                    method=subprocess.check_call)


def check_output_git(command, local=True, static_dir=""):
    return call_git(command, local=local, static_dir=static_dir,
                    method=subprocess.check_output)


def get_phab_data(phab_id):
    arc_process = subprocess.Popen(
        ["arc", "call-conduit", "differential.getdiff"],
        shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        close_fds=True)
    phab_data = arc_process.communicate('{"revision_id": "%s"}' % phab_id)[0]
    return json.loads(phab_data)


def get_phab_base_revision(phab_data):
    return phab_data['response']['sourceControlBaseRevision']


def get_phab_project_name(phab_data):
    return phab_data['response']['projectName']


def is_valid_phab_review(phab_id):
    reviews = PhabricatorReview.objects.filter(review_id=phab_id)
    if len(reviews) > 0:
        review = reviews[0]
        return review.exercise_related

    phab_data = get_phab_data(phab_id)
    project_name = get_phab_project_name(phab_data)
    base_revision = get_phab_base_revision(phab_data)

    new_review = PhabricatorReview(review_id=phab_id)

    # TODO(emily): at some point, when all reviews have this, we can simply
    # ignore phabricator reviews without the correct project name
    if project_name == "khan-exercises":
        new_review.exercise_related = True
    elif not base_revision:
        new_review.exercise_related = False
    elif call_git(["show", "-s", "--format=%H", base_revision]) == 0:
        new_review.exercise_related = True
    else:
        new_review.exercise_related = False

    new_review.save()

    return new_review.exercise_related


def home(request):
    check_call_git(["fetch", "-p", "origin"])

    branch_prefix = "refs/remotes/origin/"
    branch_list = check_output_git(
        ["for-each-ref", "--format=%(refname)", branch_prefix + "*"])

    branch_list = branch_list.rstrip('\n').split("\n")

    branches = []

    for branch in sorted(branch_list):
        if not branch.startswith(branch_prefix):
            raise Exception("Branch %r doesn't start with %r" %
                            (branch, branch_prefix))

        branch = branch[len(branch_prefix):]

        if branch == "HEAD":
            continue

        branches.append({
            'name': branch,
            'path': "origin:" + branch
        })

    with closing(urlopen(
            "https://api.github.com/repos/%s/%s/pulls?per_page=30" %
            (settings.SANDCASTLE_USER, settings.SANDCASTLE_REPO))) as u:
        pull_data = u.read()

    arc_process = subprocess.Popen(
        ["arc", "call-conduit", "differential.query"],
        shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        close_fds=True)
    phab_data = arc_process.communicate('{"status": "status-open"}')[0]

    pulls = json.loads(pull_data)
    test_phabs = json.loads(phab_data)

    unsorted_phabs = []

    for phab in test_phabs["response"]:
        phab_id = phab["id"]
        if is_valid_phab_review(phab_id):
            unsorted_phabs.append(phab)

    phabs = sorted(unsorted_phabs, key=lambda phab: phab["id"], reverse=True)

    context = {
        'pulls': pulls,
        'branches': branches,
        'phabs': phabs,
    }

    return render_to_response(
        "home.html",
        context,
        context_instance=RequestContext(request),
    )


def render_diff(patch):
    r_filename = re.compile(r'(?<=^\+\+\+ b/)(.+)$', re.MULTILINE)
    all_files = r_filename.findall(patch)

    patch = pygments.highlight(
        patch,
        pygments.lexers.DiffLexer(),
        pygments.formatters.HtmlFormatter())

    patch_linked = html.mark_safe(patch)

    return [all_files, patch_linked]


def update_static_dir(user, branch):
    if user == "":
        static_dir = branch
        local_branch = branch
        remote_branch = ""
        local = True
    else:
        static_dir = "%s:%s" % (user, branch)
        local_branch = "%s##%s" % (user, branch)
        remote_branch = "refs/remotes/%s/%s" % (user, branch)
        local = False

    if not local:
        check_call_git(["branch", "-f", local_branch, remote_branch])

    if local:
        shutil.rmtree(make_base_dir(False, static_dir), ignore_errors=True)

    if not os.path.isdir(make_base_dir(False, static_dir)):
        subprocess.check_call(["git", "clone", "--depth", "1",
            "file://" + make_base_dir(), "--branch", local_branch,
            make_base_dir(False, static_dir)])
    else:
        check_call_git(["fetch", "origin", local_branch], local=False,
            static_dir=static_dir)
        check_call_git(["checkout", "FETCH_HEAD"], local=False,
            static_dir=static_dir)


def phab(request, id=None):
    check_call_git(["fetch", "origin"])

    if not is_valid_phab_review(id):
        return HttpResponseForbidden(
            "<h1>Error</h1><p>D%s is not a khan-exercises review.</p>" % id)

    patch_name = "D" + id
    branch_name = "arcpatch-" + patch_name
    new_branch_name = branch_name + "-new"

    with open("/tmp/phab.lock", 'w') as lock_file:
        fcntl.lockf(lock_file, fcntl.LOCK_EX)

        os.chdir(os.path.join(settings.PROJECT_DIR, "media", "repo"))

        # arc gets confused if this file doesn't exist with the proper contents
        if not os.path.isfile('.git/arc/default-relative-commit'):
            try:
                os.mkdir('.git/arc')
            except OSError, e:
                if e.errno == errno.EEXIST:
                    pass
                else:
                    raise
            with open('.git/arc/default-relative-commit', 'w') as f:
                f.write('origin/master')

        try:
            phab_data = get_phab_data(id)
            check_call_git(["checkout", "--quiet",
                get_phab_base_revision(phab_data)])
            check_call_git(["checkout", "--quiet", "-b", new_branch_name])
            subprocess.check_call(["arc", "patch", "--nobranch", patch_name])
            check_call_git(["branch", "-M", new_branch_name, branch_name])
            check_call_git(["checkout", "--quiet", "master"])
        except subprocess.CalledProcessError, e:
            check_call_git(["checkout", "--quiet", "master"])
            call_git(["branch", "-D", new_branch_name])
            raise Http404

        os.chdir(settings.PROJECT_DIR)

        update_static_dir("", branch_name)

        fcntl.lockf(lock_file, fcntl.LOCK_UN)

    patch = check_output_git(["diff", "refs/remotes/origin/master..."
                              "refs/heads/" + branch_name])

    all_files, patch_linked = render_diff(patch)

    context = {
        'title': patch_name,
        'patch': patch_linked,
        'all_files': all_files,
        'castle': "/media/castles/%s" % branch_name,
        'branch': branch_name,
        'link': "http://phabricator.khanacademy.org/%s" % patch_name
    }

    return render_to_response(
        'diff.html',
        context,
        context_instance=RequestContext(request),
    )


def pull(request, number=None):
    user = settings.SANDCASTLE_USER

    try:
        with closing(urlopen(
                "https://api.github.com/repos/%s/%s/pulls/%s" %
                (settings.SANDCASTLE_USER, settings.SANDCASTLE_REPO,
                 number))) as u:
            pull_data = u.read()
    except HTTPError:
        raise Http404
    pull_data = json.loads(pull_data)
    user, branch = pull_data['head']['label'].split(":")

    # Don't check_call the "git remote add"; we expect it to fail if the remote
    # exists already
    call_git(["remote", "add", user,
              "git://github.com/%s/%s.git" % (user, settings.SANDCASTLE_REPO)])
    check_call_git(["fetch", user])

    update_static_dir(user, branch)

    with closing(urlopen(pull_data['diff_url'])) as u:
        patch = encoding.force_unicode(u.read(), errors='ignore')

    all_files, patch_linked = render_diff(patch)

    context = {
        'title': pull_data['title'],
        'body': pull_data['body'],
        'patch': patch_linked,
        'all_files': all_files,
        'castle': "/media/castles/%s:%s" % (user, branch),
        'branch': "%s:%s" % (user, branch),
        'link': pull_data['html_url'],
    }

    return render_to_response(
        'diff.html',
        context,
        context_instance=RequestContext(request),
    )


def branch(request, branch=None):
    user = settings.SANDCASTLE_USER

    if ":" in branch:
        user, branch = branch.split(":")
    else:
        user = "origin"

    # Don't check_call the "git remote add"; we expect it to fail if the remote
    # exists already
    call_git(["remote", "add", user, "git://github.com/%s/%s.git" %
             (user, settings.SANDCASTLE_REPO)])
    check_call_git(["fetch", user])

    update_static_dir(user, branch)

    patch = check_output_git(["diff", "refs/remotes/origin/master..."
                              "refs/remotes/" + user + "/" + branch])

    all_files, patch_linked = render_diff(patch)

    context = {
        'title': branch,
        'patch': patch_linked,
        'all_files': all_files,
        'castle': "/media/castles/%s:%s" % (user, branch),
        'branch': "%s:%s" % (user, branch),
    }

    return render_to_response(
        'diff.html',
        context,
        context_instance=RequestContext(request),
    )

