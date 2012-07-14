from urllib2 import urlopen, HTTPError
from contextlib import closing
import os
import re
from subprocess import call, check_output
import subprocess
import mimetypes

from django.shortcuts import render_to_response
from django.template import RequestContext
from django.http import Http404, HttpResponse
from django.utils import simplejson, html, encoding
from django.conf import settings

def dirserve(request, branch="", path=""):
    base_dir = os.path.join(settings.PROJECT_DIR, "media", "master")
    git_dir = os.path.join(base_dir, ".git")

    if ":" in branch:
        user, branch = branch.split(":")
    else:
        user = 'origin'

    print user, branch

    if call(["git", "--git-dir", git_dir, "--work-tree", base_dir, "show-ref", "--verify", "--quiet", "refs/remotes/" + user + "/" + branch]) != 0:
        raise Http404

    files = subprocess.check_output(["git", "--git-dir", git_dir, "--work-tree", base_dir, "ls-tree", "-z", "--name-only", user + "/" + branch + ":" + path])

    files = ['<a href="%s">%s</a><br>' % (f, f)
             for f in ['..'] + files.split('\0')]

    output = ["<h1>Directory for <strong>" + branch + "/" + path + "/</strong></h1>"] + files

    return HttpResponse(output)

def fileserve(request, branch="", path=""):
    base_dir = os.path.join(settings.PROJECT_DIR, "media", "master")
    git_dir = os.path.join(base_dir, ".git")

    if ":" in branch:
        user, branch = branch.split(":")
    else:
        user = 'origin'

    print user, branch

    if call(["git", "--git-dir", git_dir, "--work-tree", base_dir, "show-ref", "--verify", "--quiet", "refs/remotes/" + user + "/" + branch]) != 0:
        raise Http404

    file = subprocess.check_output(["git", "--git-dir", git_dir, "--work-tree", base_dir, "show", user + "/" + branch + ":" + path])
    type = mimetypes.guess_type(request.path)[0]

    return HttpResponse(file, content_type=type)

def home(request):
    base_dir = os.path.join(settings.PROJECT_DIR, "media", "master")
    git_dir = os.path.join(base_dir, ".git")

    branch_prefix = "refs/remotes/origin/"
    branch_list = subprocess.check_output(["git", "--git-dir", git_dir, "--work-tree", base_dir, "for-each-ref", "--format=%(refname)", branch_prefix + "*"])

    branch_list = branch_list.strip().split("\n")

    branches = []

    for branch in branch_list:
        if not branch.startswith(branch_prefix):
            raise Exception("Branch %r doesn't start with %r" % (branch, branch_prefix))

        branch = branch[len(branch_prefix):]

        if branch == "HEAD":
            continue

        branches.append({
            'name': branch,
        })

    with closing(urlopen("https://api.github.com/repos/%s/%s/pulls?per_page=100" % (settings.SANDCASTLE_USER, settings.SANDCASTLE_REPO))) as u:
        pull_data = u.read()

    arc_process = subprocess.Popen(["arc", "call-conduit", "differential.query"], shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True)
    phab_data = arc_process.communicate('{"status": "status-open"}')[0]

    pulls = simplejson.loads(pull_data)
    phabs = simplejson.loads(phab_data)

    context = {
        'pulls': pulls,
        'branches': branches,
        'phabs': phabs,
    }

    return render_to_response(
        "home.html",
        context,
        context_instance = RequestContext(request),
    )

def phab(request, id=None):
    return ""

def pull(request, number=None):
    user = settings.SANDCASTLE_USER

    base_dir = os.path.join(settings.PROJECT_DIR, "media", "master")
    git_dir = os.path.join(base_dir, ".git")

    try:
        with closing(urlopen("https://api.github.com/repos/%s/%s/pulls/%s" % (settings.SANDCASTLE_USER, settings.SANDCASTLE_REPO, number))) as u:
            pull_data = u.read()
    except HTTPError:
        raise Http404
    pull_data = simplejson.loads(pull_data)
    user, branch = pull_data['head']['label'].split(":")

    branch_list = subprocess.check_output(["git", "--git-dir", git_dir, "--work-tree", base_dir, "for-each-ref", "--format=%(refname)", branch_prefix + "*"])

    name = "%s:%s" % (user, branch)
    castle = "/castles/%s" % name

    os.chdir(os.path.join(settings.PROJECT_DIR, 'media/castles'))
    if os.path.isdir(name):
        os.chdir(name)
        call(["git", "fetch", "origin", branch])
        call(["git", "reset", "--hard", "FETCH_HEAD"])
    else:
        call(["git", "clone", "--branch=%s" % branch, "git://github.com/%s/%s.git" % (user, settings.SANDCASTLE_REPO), name])
        os.chdir(name)

    with closing(urlopen(pull_data['diff_url'])) as u:
        patch = encoding.force_unicode(u.read(), errors='ignore')

    patch = html.escape(patch)
    r_filename = re.compile(r'(?<=^\+\+\+ b/)(.+)$', re.MULTILINE)
    all_files = r_filename.findall(patch)
    patch = r_filename.sub(r'<a href="%s/\1">\1</a>' % castle, patch, 0)
    patch_linked = html.mark_safe(patch)

    context = {
        'title': pull_data['title'],
        'body': pull_data['body'],
        'patch': patch_linked,
        'all_files': all_files,
        'castle': castle,
    }

    return render_to_response(
        'pull.html',
        context,
        context_instance = RequestContext(request),
    )

def branch(request, branch=None):
    user = settings.SANDCASTLE_USER

    if ":" in branch:
        user, branch = branch.split(":")
    else:
        user = "origin"

    name = "%s:%s" % (user, branch)
    castle = "/castles/%s" % name

    os.chdir(os.path.join(settings.PROJECT_DIR, 'media/master'))
    call(["git", "remote", "add", user, "git://github.com/%s/%s.git" % (user, settings.SANDCASTLE_REPO)])
    call(["git", "fetch", user])

    patch = check_output(["git", "diff", "refs/remotes/origin/master...refs/remotes/" + user + "/" + branch])

    patch = html.escape(patch)
    r_filename = re.compile(r'(?<=^\+\+\+ b/)(.+)$', re.MULTILINE)
    all_files = r_filename.findall(patch)
    patch = r_filename.sub(r'<a href="%s/\1">\1</a>' % castle, patch, 0)
    patch_linked = html.mark_safe(patch)

    context = {
        'title': "%s:%s" % (user, branch),
        'body': "",
        'patch': patch_linked,
        'all_files': all_files,
        'castle': castle,
    }

    return render_to_response(
        'pull.html',
        context,
        context_instance = RequestContext(request),
    )
