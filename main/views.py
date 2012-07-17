from urllib2 import urlopen, HTTPError
from contextlib import closing
import errno
import json
import os
import re
import subprocess
import mimetypes

from django.shortcuts import render_to_response, redirect
from django.template import RequestContext
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.utils import html, encoding
from django.conf import settings
import pygments
import pygments.lexers
import pygments.formatters

from models import PhabricatorReview

base_dir = os.path.join(settings.PROJECT_DIR, "media", "repo")
git_dir = os.path.join(base_dir, ".git")


def call_git(command, method=subprocess.call):
    return method(
        ["git", "--git-dir", git_dir, "--work-tree", base_dir] + command)


def check_call_git(command):
    return call_git(command, subprocess.check_call)


def check_output_git(command):
    return call_git(command, subprocess.check_output)


def blob_or_tree(user, branch, path):
    if len(path) == 0:
        return "tree"

    if user:
        info = check_output_git([
            "ls-tree", "refs/remotes/%s/%s" % (user, branch), path])
    else:
        info = check_output_git(["ls-tree", "refs/heads/%s" % branch, path])

    return info.split(None, 3)[1]


def is_valid_phab_review(phab_id):
    reviews = PhabricatorReview.objects.filter(review_id=phab_id)
    if len(reviews) > 0:
        review = reviews[0]
        if review.exercise_related:
            return True

    arc_process = subprocess.Popen(
        ["arc", "call-conduit", "differential.getdiff"],
        shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        close_fds=True)
    phab_data = arc_process.communicate('{"revision_id": "%s"}' % phab_id)[0]
    phab_data = json.loads(phab_data)

    base_revision = phab_data['response']['sourceControlBaseRevision']

    new_review = PhabricatorReview(review_id=phab_id)

    if call_git(["show", "-s", "--format=%H", base_revision]) == 0:
        new_review.exercise_related = True
    else:
        new_review.exercise_related = False

    new_review.save()

    return new_review.exercise_related


def fileserve(request, branch="", path=""):
    origbranch = branch

    if ":" in branch:
        user, branch = branch.split(":")
        local = False
        ref = "refs/remotes/%s/%s" % (user, branch)
    else:
        user = ""
        local = True
        ref = "refs/heads/%s" % branch

    try:
        check_call_git(["show-ref", "--verify", "--quiet", ref])
    except subprocess.CalledProcessError:
        raise Http404

    path = path.strip('/')

    if blob_or_tree(user, branch, path) == "tree":
        if local:
            file_list = check_output_git([
                "ls-tree", "-z", "%s:%s" % (branch, path)])
        else:
            file_list = check_output_git([
                "ls-tree", "-z", "%s/%s:%s" % (user, branch, path)])

        file_list = file_list.strip('\0').split('\0')

        files = []

        for f in file_list:
            _, b_or_t, _, name = f.split(None)

            if b_or_t == 'tree':
                name += '/'

            files.append(name)

        if path:
            files.insert(0, '..')

        files = ['<a href="%s">%s</a><br>' % (f, f) for f in files]

        output = ["<h1>Directory for <strong>%s/%s%s</strong></h1>" %
                  (origbranch, path, '' if path == '' else '/')] + files

        return HttpResponse(output)
    else:
        if local:
            file = check_output_git(["show", branch + ":" + path])
        else:
            file = check_output_git(["show", user + "/" + branch + ":" + path])
        type = mimetypes.guess_type(request.path)[0]

        return HttpResponse(file, content_type=type)


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
            "https://api.github.com/repos/%s/%s/pulls?per_page=100" %
            (settings.SANDCASTLE_USER, settings.SANDCASTLE_REPO))) as u:
        pull_data = u.read()

    arc_process = subprocess.Popen(
        ["arc", "call-conduit", "differential.query"],
        shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        close_fds=True)
    phab_data = arc_process.communicate('{"status": "status-open"}')[0]

    pulls = json.loads(pull_data)
    test_phabs = json.loads(phab_data)

    phabs = []

    for phab in test_phabs["response"]:
        phab_id = phab["id"]
        reviews = PhabricatorReview.objects.filter(review_id=phab_id)
        if len(reviews) > 0:
            review = reviews[0]
            if review.exercise_related:
                phabs.append(phab)
        else:
            if is_valid_phab_review(phab_id):
                phabs.append(phab)

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


def render_diff(request, title, body, patch, user, branch):
    if user:
        name = "%s:%s" % (user, branch)
    else:
        name = branch
    castle = "/castles/%s" % name

    r_filename = re.compile(r'(?<=^\+\+\+ b/)(.+)$', re.MULTILINE)
    all_files = r_filename.findall(patch)

    patch = pygments.highlight(
        patch,
        pygments.lexers.DiffLexer(),
        pygments.formatters.HtmlFormatter())

    patch_linked = html.mark_safe(patch)

    context = {
        'title': title,
        'body': body,
        'patch': patch_linked,
        'all_files': all_files,
        'castle': castle,
        'branch': name,
    }

    return render_to_response(
        'diff.html',
        context,
        context_instance=RequestContext(request),
    )


def phab(request, id=None):
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

    if not is_valid_phab_review(id):
        return HttpResponseForbidden(
            "<h1>Error</h1><p>D%s is not a khan-exercises review.</p>" % id)

    patch_name = "D" + id
    branch_name = "arcpatch-" + patch_name
    new_branch_name = branch_name + "-new"

    check_call_git(["checkout", "master"])
    try:
        check_call_git(["checkout", "-b", new_branch_name])
        subprocess.check_call(["arc", "patch", "--nobranch", patch_name])
        check_call_git(["branch", "-M", new_branch_name, branch_name])
        check_call_git(["checkout", "master"])
    except subprocess.CalledProcessError, e:
        call_git(["branch", "-D", new_branch_name])
        check_call_git(["checkout", "master"])
        raise Http404

    patch = check_output_git(["diff", "refs/remotes/origin/master..."
                              "refs/heads/" + branch_name])

    os.chdir(settings.PROJECT_DIR)

    return render_diff(request, patch_name, "", patch, "", branch_name)


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

    with closing(urlopen(pull_data['diff_url'])) as u:
        patch = encoding.force_unicode(u.read(), errors='ignore')

    return render_diff(request, pull_data['title'], pull_data['body'], patch,
                       user, branch)


def branch(request, branch=None):
    user = settings.SANDCASTLE_USER

    title = branch

    if ":" in branch:
        user, branch = branch.split(":")
    else:
        user = "origin"

    # Don't check_call the "git remote add"; we expect it to fail if the remote
    # exists already
    call_git(["remote", "add", user, "git://github.com/%s/%s.git" %
             (user, settings.SANDCASTLE_REPO)])
    check_call_git(["fetch", user])

    patch = check_output_git(["diff", "refs/remotes/origin/master..."
                              "refs/remotes/" + user + "/" + branch])

    return render_diff(request, title, "", patch, user, branch)


def castle_redirect(request, branch="", path=""):
    return redirect('fileserve', branch=branch, path=path, permanent=True)
