#!/usr/bin/env python3

# The MIT License (MIT)
# Copyright (c) 2017 Apertus Solutions, LLC
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
# OR OTHER DEALINGS IN THE SOFTWARE.

import json
import re
import sys
import os
import argparse
import requests
import pygit2
import sh

from datetime import datetime
from pprint import PrettyPrinter
 
class Issues:
    all_types = ['New Feature', 'Improvement', 'Bug', 'Task', 'Sub-task', 'Story', 'Epic', 'No Assigned Issue']
    feature_types = ['New Feature', 'Improvement']
    maint_types = ['Bug', 'Task', 'Sub-task', 'Story', 'Epic', 'No Assigned Issue']
    issues = {}

    @staticmethod
    def get_issue(issue):
        if not issue in Issues.issues:
            url = "https://openxt.atlassian.net/rest/api/latest/issue/" + issue
            try:
                r = requests.get(url)
                
                if r.status_code == requests.codes.ok:
                    Issues.issues[issue] = json.loads(r.text)
                else:
                    sys.stderr.write("Failed to query JIRA, %s\n" % url)
                    Issues.issues[issue] = []
            except:
                sys.stderr.write("Exception fetching JIRA issue %s\n" % issue)
                Issues.issues[issue] = []

        return Issues.issues[issue]

class CommitEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Commit):
            return {
                "hash" : obj.hash,
                "repo" : obj.repo,
                "subject" : obj.subject,
                "body" : obj.body,
                "commit_date" : obj.commit_date,
                "author_name" : obj.author_name,
                "author_email" : obj.author_email,
                "signers" : obj.signers
            }

class Commit:
    def __init__(self, repo_name, git_commit):
        issues = list(set(re.findall('OXT-[0-9]+', git_commit.message)))
        signed = re.findall('Signed-off-by: (.*)\n', git_commit.message)
        signed.extend(re.findall('Signed off by: (.*)\n', git_commit.message))

        msgsplt = git_commit.message.split('\n\n', 1)
        if len(msgsplt) == 2:
            subject = msgsplt[0].strip()
            body = msgsplt[1].strip()
        else:
            subject = msgsplt[0].strip()
            body = ""

        self.hash = git_commit.hex
        self.repo = repo_name
        self.subject = subject.replace("{", "\{")
        self.body = body
        self.raw = git_commit.message
        self.commit_date = datetime.utcfromtimestamp(
            git_commit.commit_time).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.author_name = git_commit.author.name
        self.author_email = git_commit.author.email
        self.parents = [c.hex for c in git_commit.parents]
        self.issues = issues
        self.signers = signed

    @staticmethod
    def is_merge(commit):
        return True if len(commit.parents) > 1 else False

    @staticmethod
    def dedup_commits(commits):
        deduped = {v.hash:v for v in commits}.values()

        return deduped

    @staticmethod
    def categorize(commits):
        report = {}

        for t in Issues.all_types:
            report[t] = []

        for commit in commits:
            if Commit.is_merge(commit):
                continue

            commit.security = False

            if not commit.issues or 'No Assigned Issue' in commit.issues:
                report['No Assigned Issue'].append(commit)
            else:
                for issue_id in commit.issues:
                    issue = Issues.get_issue(issue_id)
                    if issue:
                        for c in issue['fields']['components']:
                            if "Security" in c['name']:
                                commit.security = True
                        report[issue['fields']['issuetype']['name']].append(commit)
                    else:
                        report['No Assigned Issue'].append(commit)

        return report

    @staticmethod
    def merge_categorized(commits1, commits2):
        report = {}

        for t in Issues.all_types:
            report[t] = commits1[t] + commits2[t]

        return report

class Repository:
    GITHUB_URL = "https://github.com/OpenXT/"

    def __init__(self, repo_name, previous, new, workdir):
        self.name = repo_name
        self.repodir = workdir + "/" + repo_name + ".git"
        self.previous = previous
        self.new = new
        self.url = self.GITHUB_URL + repo_name + ".git"

        # Check the remote for refs before attempting anything
        try:
            pc = sh.wc(sh.git("ls-remote", "--heads", "--tags", self.GITHUB_URL + repo_name, previous), "-l").stdout.decode('utf-8').strip()
            nc = sh.wc(sh.git("ls-remote", "--heads", "--tags", self.GITHUB_URL + repo_name, new), "-l").stdout.decode('utf-8').strip()

            if pc == "0":
                sys.stderr.write("No tag or head %s for repository %s\n", previous, self.GITHUB_URL + repo_name)
                raise Error
            if nc == "0":
                sys.stderr.write("No tag or head %s for repository %s\n", new, self.GITHUB_URL + repo_name)
                raise Error

        except sh.ErrorReturnCode as e:
            print(e)
            raise Error

        if not os.path.isdir(self.repodir):
            try:
                sh.git.clone("--mirror", self.url, self.repodir)
            except sh.ErrorReturnCode:
                sys.stderr.write("Failed to mirror repo url: %s\n" % self.url)
                raise Error

        try:
            repo_path = pygit2.discover_repository(self.repodir)
            self.repo = pygit2.Repository(repo_path)
        except KeyError:
            sys.stderr.write("%s is not a git repository\n" % name)
            raise RuntimeError

    def get_commits(self):
        self.commits = []

        try:
            cherry = filter(None, sh.git.cherry(self.previous, self.new, _cwd=self.repodir).stdout.decode('utf-8').split("\n"))
            cherry = filter(lambda i: i[0] == "+", cherry)
            if cherry:
                commit_list = [x[2:].strip() for x in cherry]
            else:
                return
        except sh.ErrorReturnCode:
            sys.stderr.write ("Error occurred generating cherry list for %s\n" % self.name)
            raise RuntimeError

        for ref in commit_list:
            if ref:
                commit = self.repo.get(ref)
                self.commits.append(Commit(self.name, commit))

    def get_contributors(self):
        self.authors = {}
        self.contributors = []

        for c in self.commits:
            if Commit.is_merge(c):
                continue

            if not c.author_email in self.authors:
                self.authors[c.author_email] = c.author_name

            for s in c.signers:
                if not s in self.contributors:
                    self.contributors.append(s)

class Release:
    FETCH_ISSUES = 1
    REPO_URL = "https://api.github.com/users/openxt/repos?per_page=100"
    REPO_BLACKLIST = [ "bats-suite", "bvt",
                       "docs", "openxt.github.io",
                       "blktap", "blktap3",
                       "bootage", "cdrom-daemon", "ocaml",
                       "meta-openxt-base", "meta-openxt-qt", "meta-openxt-remote-management", "meta-selinux"
                    ]

    def __init__(self, previous, new, workdir, flags=0):
        self.workdir = workdir
        self.previous = previous
        self.new = new
        self.flags = flags

        try:
            repourl = sh.curl("-s", self.REPO_URL)
            jq_raw = sh.jq(repourl, "-M", ".[].name")
            repo_list = list(filter(None, jq_raw.replace("\"", "").split("\n")))
        except sh.ErrorReturnCode:
            sys.stderr.write("Failed retrieving OpenXT repository list from Github.")
            raise Error

        self.repos = []
        whitelisted = lambda x: not x in self.REPO_BLACKLIST
        for r in list(filter(whitelisted, repo_list)):
            try:
                self.repos.append(Repository(r,previous,new,workdir))
            except:
                continue

        if not self.repos:
            sys.stderr.write("Unable to find any repo with both references, %s and %s.\n" % (previous, new))
            raise Error

    def generate(self):
        self.categorized = {}
        self.contributors = []

        for t in Issues.all_types:
            self.categorized[t] = []

        for r in self.repos:
            r.get_commits()

            if not r.commits:
                continue

            for c in r.commits:
                if c.issues and self.flags & self.FETCH_ISSUES:
                    for i in c.issues:
                        Issues.get_issue(i)

            report = Commit.categorize(r.commits)
            self.categorized = Commit.merge_categorized(self.categorized, report)

            r.get_contributors()
            self.contributors.extend(r.contributors)

        self.contributors = list(set(self.contributors))

class ReleaseDocument:
    def __init__(self, filepath="release.adoc", relnum="X.Y.Z", author="Author Name", email="author@email.com", entity="copyright holding entity", rev="1.0", rev_string="First"):
        self.relnum = relnum
        self.author = author
        self.email = email
        self.entity = entity
        self.rev = rev
        self.rev_string = rev_string

        self.fd = open(filepath, 'w')

    def close(self):
        self.fd.close()

    def header_page(self):
        fd = self.fd

        fd.write("OpenXT %s Release\n====================\n" % self.relnum)
        fd.write("%s <%s>\n" % (self.author, self.email))
        fd.write("v%s, %s: %s revision\n" % (self.rev, datetime.today().strftime("%B %Y"),self.rev_string))
        fd.write(":toc:\n\n")
        fd.flush()

    def platform_page(self, path):
        fd = self.fd

        fd.write(":numbered:\nPlatform\n--------\n")
        fd.write("\n")

        if path:
            with open(path, 'r') as body:
                fd.write(body.read())

        fd.write("\n")
        fd.write("<<<\n")
        fd.write("\n")
        fd.flush()

    def features_page(self, categorized):
        fd = self.fd
        commits = []

        for t in Issues.feature_types:
            commits.extend(categorized[t])

        commits = Commit.dedup_commits(commits)

        fd.write(":numbered:\nFeature Additions\n-----------------\n")
        fd.write("\n")

        for c in commits:
            fd.write("- https://github.com/OpenXT/%s/commit/%s[%s/%s]: %s" % (c.repo, c.hash, c.repo, c.hash[0:8], c.subject))
            if c.issues:
                fd.write(", ")
                for i in c.issues:
                    fd.write("https://openxt.atlassian.net/browse/%s[%s] " % (i,i))
            fd.write("\n")

        fd.write("\n")
        fd.write("<<<\n")
        fd.write("\n")
        fd.flush()

    def security_page(self, categorized):
        fd = self.fd
        commits = []

        for t in Issues.all_types:
            for c in categorized[t]:
                if c.security:
                    commits.append(c)
        commits = Commit.dedup_commits(commits)

        fd.write(":numbered:\nSecurity Fixes\n--------------\n")
        fd.write("\n")

        for c in commits:
            fd.write("- https://github.com/OpenXT/%s/commit/%s[%s/%s]: %s" % (c.repo, c.hash, c.repo, c.hash[0:8], c.subject))
            if c.issues:
                fd.write(", ")
                for i in c.issues:
                    fd.write("https://openxt.atlassian.net/browse/%s[%s] " % (i,i))
            fd.write("\n")

        fd.write("\n")
        fd.write("<<<\n")
        fd.write("\n")
        fd.flush()

    def maintenance_page(self, categorized):
        fd = self.fd
        commits = []

        for t in Issues.maint_types:
            commits += categorized[t]
        commits = Commit.dedup_commits(commits)

        fd.write(":numbered:\nMaintenance Changes\n-------------------\n")
        fd.write("\n")

        for c in commits:
            fd.write("- https://github.com/OpenXT/%s/commit/%s[%s/%s]: %s" % (c.repo, c.hash, c.repo, c.hash[0:8], c.subject))
            if c.issues:
                fd.write(", ")
                for i in c.issues:
                    fd.write("https://openxt.atlassian.net/browse/%s[%s] " % (i,i))
            fd.write("\n")

        fd.write("\n")
        fd.write("<<<\n")
        fd.write("\n")
        fd.flush()

    def testing_page(self, path):
        fd = self.fd

        fd.write(":numbered:\nTesting\n-------\n")
        fd.write("\n")

        if path:
            with open(path, 'r') as body:
                fd.write(body.read())

        fd.write("\n")
        fd.write("<<<\n")
        fd.write("\n")
        fd.flush()

    def known_issues_page(self, path):
        fd = self.fd

        fd.write(":numbered:\nKnown Issues\n------------\n")
        fd.write("\n")

        if path:
            with open(path, 'r') as body:
                fd.write(body.read())

        fd.write("\n")
        fd.write("<<<\n")
        fd.write("\n")
        fd.flush()

    def contributors_page(self, contributors):
        fd = self.fd

        fd.write(":numbered:\nContributors\n------------\n")
        fd.write("\n")

        for c in contributors:
            fd.write("- %s" % c) 
            fd.write("\n")

        fd.write("\n")
        fd.write("<<<\n")
        fd.write("\n")
        fd.flush()

    def license_page(self):
        fd = self.fd

        fd.write("[appendix]\nLicense\n-------\n")
        fd.write("Copyright %s by <%s>. " % (datetime.now().year, self.entity))
        fd.write("Created by %s <%s>. " % (self.author, self.email))
        fd.write("This work is licensed under the Creative Commons " +
                 "Attribution 4.0 International License. To view a copy of " +
                 "this license, visit http://creativecommons.org/licenses/by/4.0/.\n")
        fd.flush()

def main(base_path, out, refs, publish, bodies, gen_json=False):
    try:
        release = Release(refs[0], refs[1], base_path, Release.FETCH_ISSUES)
    except:
        sys.stderr.write("Abort...\n")
        sys.exit(1)

    release.generate()

    if gen_json:
        json_data = {}
        for r in release.repos:
            json_data[r.name] = r.commits

        with open('commits.json', 'w') as fd:
            json.dump(json_data, fd, cls=CommitEncoder)

    doc = ReleaseDocument(filepath=out)

    if publish['relnum']:
        doc.relnum = publish['relnum']
    if publish['author']:
        doc.author = publish['author']
    if publish['email']:
        doc.email = publish['email']
    if publish['entity']:
        doc.entity = publish['entity']

    doc.header_page()
    doc.platform_page(bodies['platform'])
    doc.features_page(release.categorized)
    doc.security_page(release.categorized)
    doc.maintenance_page(release.categorized)
    doc.testing_page(bodies['testing'])
    doc.known_issues_page(bodies['known'])
    doc.contributors_page(release.contributors)
    doc.license_page()
    doc.close()
 

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", help="directory where git repositories will be stored")
    parser.add_argument("-A", "--author", help="author's name")
    parser.add_argument("-E", "--email", help="author's email")
    parser.add_argument("-G", "--entity", help="copyright holding entity")
    parser.add_argument("-R", "--relnum", help="release version number")
    parser.add_argument("-P", "--platform", help="path to file with \"Platform\" body")
    parser.add_argument("-T", "--testing", help="path to file with \"Testing\" body")
    parser.add_argument("-K", "--known", help="path to file with \"Known Issues\" body")
    parser.add_argument("-o", "--output", help="file name for asciidoc file")
    parser.add_argument("-j", "--json", action="store_true", help="generate json file of commits")
    parser.add_argument("previous", help="the git reference for previous release")
    parser.add_argument("new", help="the git reference for the new release")

    args = parser.parse_args()
    if args.new:
        refs = [args.previous, args.new]
    else:
        usage()

    publish = {}
    bodies = {}

    path = args.path if args.path else "repos"
    output = args.output if args.output else "release.adoc"
    gen_json = args.json if args.json else False

    publish['author'] = args.author if args.author else ""
    publish['email'] = args.email if args.email else ""
    publish['entity'] = args.entity if args.entity else ""
    publish['relnum'] = args.relnum if args.relnum else ""

    bodies['platform'] = args.platform if args.platform else ""
    bodies['testing'] = args.testing if args.testing else ""
    bodies['known'] = args.known if args.known else ""

    main(path, output, refs, publish, bodies, gen_json)
