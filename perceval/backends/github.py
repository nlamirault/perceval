# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#



import json
import logging
import os.path

import requests

from ..backend import Backend, BackendCommand
from ..cache import Cache
from ..errors import CacheError
from ..utils import DEFAULT_DATETIME, str_to_datetime

logger = logging.getLogger(__name__)

class GitHub(Backend):
    """GitHub backend for Perceval.

    This class allows the fetch the issues stored in GitHub
    repository.

    :param owner: GitHub owener
    :param repository: GitHub repository from the owner
    :param token: GitHub auth token to access the API
    :param base_url: GitHub URL in enterprise edition case
    :param cache: Use issues already retrieved in cache
    """

    def __init__(self, owner=None, repository=None, token=None,
                 base_url=None, cache=None):
        super().__init__(cache=cache)
        self.owner = owner
        self.repository = repository
        self.client = GitHubClient(owner, repository, token, base_url)

    def fetch(self, from_date=DEFAULT_DATETIME):
        """Fetch the issues from the repository.

        The method retrieves, from a GitHub repository, the issues
        updated since the given date.

        :param from_date: obtain issues updated since this date

        :returns: a generator of issues
        """

        self._purge_cache_queue()

        issues_groups = self.client.get_issues(from_date)

        for raw_issues in issues_groups:
            self._push_cache_queue(raw_issues)
            self._flush_cache_queue()
            issues = json.loads(raw_issues)
            for issue in issues:
                yield issue

    def fetch_from_cache(self):
        """Fetch the issues from the cache.

        It returns the issues stored in the cache object provided during
        the initialization of the object. If this method is called but
        no cache object was provided, the method will raise a `CacheError`
        exception.

        :returns: a generator of items

        :raises CacheError: raised when an error occurs accessing the
            cache
        """
        if not self.cache:
            raise CacheError(cause="cache instance was not provided")

        cache_items = self.cache.retrieve()

        for raw_issues in cache_items:
            issues = json.loads(raw_issues)
            for issue in issues:
                yield issue


class GitHubClient:

    def __init__(self, owner, repository, token, base_url = None):
        self.owner = owner
        self.repository = repository
        self.auth_token = token
        self.base_url = base_url

    def _get_url(self):
        github_api = "https://api.github.com"
        if self.base_url:
            github_api = self.base_url
        github_api_repos = github_api + "/repos"
        url_repo = github_api_repos + "/" + self.owner + "/" + self.repository
        return url_repo

    def _get_issues_url(self, startdate=None):
        # 100 in other items. 20 for pull requests. 30 issues
        github_per_page = 30

        url_issues = self._get_url() + "/issues"

        url_params = "?per_page=" + str(github_per_page)
        url_params += "&state=all"  # open and close pull requests
        url_params += "&sort=updated"  # sort by last updated
        url_params += "&direction=asc"  # first older pull request
        if startdate:
            startdate = startdate.isoformat()
            url_params += "&since=" + startdate

        url = url_issues + url_params

        return url

    def get_issues(self, start=None):
        """ Return the items from github API using links pagination """

        page = 0  # current page
        last_page = None  # last page
        url_next = self._get_issues_url(start)

        logger.debug("Get GitHub issues from " + url_next)
        r = requests.get(url_next, verify=False,
                         headers={'Authorization': 'token ' + self.auth_token})
        issues = r.text
        page += 1
        logger.debug("Rate limit: %s" % (r.headers['X-RateLimit-Remaining']))

        if 'last' in r.links:
            last_url = r.links['last']['url']
            last_page = last_url.split('&page=')[1].split('&')[0]
            last_page = int(last_page)
            logger.debug("Page: %i/%i" % (page, last_page))

        while issues:
            yield issues

            issues = None

            if 'next' in r.links:
                url_next = r.links['next']['url']  # Loving requests :)

                r = requests.get(url_next, verify=False,
                                 headers={'Authorization': 'token ' + self.auth_token})
                page += 1
                issues = r.text
                logger.debug("Page: %i/%i" % (page, last_page))
                logger.debug("Rate limit: %s" %
                     (r.headers['X-RateLimit-Remaining']))

class GitHubCommand(BackendCommand):
    """Class to run GitHub backend from the command line."""

    def __init__(self, *args):
        super().__init__(*args)

        self.owner = self.parsed_args.owner
        self.repository = self.parsed_args.repository
        self.token = self.parsed_args.token
        self.from_date = str_to_datetime(self.parsed_args.from_date)
        self.outfile = self.parsed_args.outfile

        if not self.parsed_args.no_cache:
            if not self.parsed_args.cache_path:
                base_path = os.path.expanduser('~/.perceval/cache/')
            else:
                base_path = self.parsed_args.cache_path
            # TODO: add get_id for backend to return the unique id
            cache_path = os.path.join(base_path, self.owner + "_" +
                                      self.repository)

            cache = Cache(cache_path)

            if self.parsed_args.clean_cache:
                cache.clean()
            else:
                cache.backup()
        else:
            cache = None

        self.backend = GitHub(self.owner, self.repository,
                              self.token, cache=cache)

    def run(self):
        """Fetch and print the issues.

        This method runs the backend to fetch the issues from the given
        repository. Bugs are converted to JSON objects and printed to the
        defined output.
        """
        if self.parsed_args.fetch_cache:
            issues = self.backend.fetch_from_cache()
        else:
            issues = self.backend.fetch(from_date=self.from_date)

        try:
            for issue in issues:
                obj = json.dumps(issue, indent=4, sort_keys=True)
                # self.outfile.write(issue['url']+"\n")
                self.outfile.write(obj)
                self.outfile.write('\n')
        except IOError as e:
            raise RuntimeError(str(e))
        except Exception as e:
            if self.backend.cache:
                self.backend.cache.recover()
            raise RuntimeError(str(e))

    @classmethod
    def create_argument_parser(cls):
        """Returns the GitHub argument parser."""

        parser = super().create_argument_parser()

        # GitHub options
        group = parser.add_argument_group('GitHub arguments')

        group.add_argument("--owner", required=True,
                           help="github owner")
        group.add_argument("--repository", required=True,
                           help="github repository")
        group.add_argument("--token", required=True,
                           help="github access token")

        return parser