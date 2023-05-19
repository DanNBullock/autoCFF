"""
The following is a script set for automatically generating a .cff file for a given repostory on github.
It is designed to functio as a GitHub action, and makes use of the cffinit package. It was initially scoped with
chatGPT using the following prompt:

User
I'd like to create a GitHub action which automatically creates a citation.cff (in accordance with the standard 
citation.cff file format described on GitHub).  In order to best capture the relevant information necessary for 
this process, it should feature a hierarchy of heuristics that seek the relevant information in the target 
repository.  The first level of information should be obtained from the API interface from GitHub itself.
Next, the documentation itself can be searched (for example, for sections which refer to authors or 
contributors).  Finally, the documentation can be searched for a DOI-like link that could be used to query the 
relevant metadata.  Could you do your best to implement this?  The actual coding sections of this (contained 
within a "main" file) should likely be engineered to be in python and run in a minimally complex python docker 
container.

"""
import os
import sys
import json
import requests
#import cffinit
import argparse
import subprocess

def getTargetRepositoryDetails(repositoryPath):
    """
    This function uses the public GitHub API to obtain the relevant information for a given repository.

    Parameters
    ----------
    repositoryPath : str
        The path to the repository on GitHub.

    Returns
    -------
    repositoryDetails : dict
        A dictionary containing the relevant information for the target repository.
    """

    # Get the repository name and owner from the repository path.
    repositoryName = repositoryPath.split("/")[-1]
    repositoryOwner = repositoryPath.split("/")[-2]

    # Get the repository details from the GitHub API.
    repositoryDetails = requests.get("https://api.github.com/repos/" + repositoryOwner + "/" + repositoryName).json()

    return repositoryDetails

def get_repository_contributors(owner, repo):
    import requests
    contributors_url = f"https://api.github.com/repos/{owner}/{repo}/contributors"
    response = requests.get(contributors_url)
    
    contributors = []
    if response.status_code == 200:
        contributors_data = response.json()
        
        for contributor_data in contributors_data:
            contributor = {
                "login": contributor_data.get("login"),
                "contributions": contributor_data.get("contributions"),
                "contributed_dates": []
            }
            
            # Retrieve contribution statistics for each contributor
            contributor_stats_url = f"https://api.github.com/repos/{owner}/{repo}/stats/contributors"
            stats_response = requests.get(contributor_stats_url)
            
            if stats_response.status_code == 200:
                stats_data = stats_response.json()
                for stats in stats_data:
                    if stats.get("author").get("login") == contributor["login"]:
                        # Calculate the total volume of code contributions
                        total_additions = sum(week.get("a", 0) for week in stats.get("weeks"))
                        total_deletions = sum(week.get("d", 0) for week in stats.get("weeks"))
                        total_changes = total_additions + total_deletions
                        
                        contributor["volume"] = total_changes
                        
                        # Retrieve the dates of contributions
                        contributor["contributed_dates"] = [
                            week.get("w") for week in stats.get("weeks") if week.get("a") or week.get("d")
                        ]
                        break

            contributors.append(contributor)
    
    return contributors

# it looks like in most cases people do not list contributors in their readme files, so developing dedicated code for this
# may not be a good use of time. 
# def parseRepositoryReadme
def search_github_user_for_orcid(username):
    import re
    import requests
    user_url = f"https://api.github.com/users/{username}"
    response = requests.get(user_url)
    
    if response.status_code == 200:
        user_data = response.json()
        
        # Search user data for ORCID-like strings
        orcid_regex = r'\bhttps?://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[0-9X]\b'
        orcid_matches = re.findall(orcid_regex, str(user_data))
        
        if orcid_matches:
            return orcid_matches
        
    # additionally, directly use the "social_accounts" API function to search for ORCID
    social_accounts_url = f"https://api.github.com/users/{username}/social_accounts"
    response = requests.get(social_accounts_url)

    # search this in the same fashion for an ORCID-like string
    if response.status_code == 200:
        social_accounts_data = response.json()
        
        # Search user data for ORCID-like strings
        orcid_regex = r'\bhttps?://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[0-9X]\b'
        orcid_matches = re.findall(orcid_regex, str(social_accounts_data))
        
        if orcid_matches:
            return orcid_matches
    
    return None

# additionally, if the user has a website, search that for an ORCID link, use the "blog" field to search for this
# theoretically this could be done by crawling the raw HTML of the linked website, but let's save this for another day

# instead, lets just leverage the the name and institution of the GitHub response API, and pipe this in to the public ORCID API
# to search for a match.

# first we have to get the name and institution from the GitHub API
def get_name_and_institution_from_github_api(username):
    import requests
    user_url = f"https://api.github.com/users/{username}"
    response = requests.get(user_url)
    
    if response.status_code == 200:
        user_data = response.json()
        
        # Search user data for ORCID-like strings
        name = user_data.get("name")
        institution = user_data.get("company")
        
        return name, institution

# then we have to pipe this in to the ORCID API
def search_orcid_individual(name, institution):
    import time

    # we're going to have to try a sequence of different possibilities
    # we begin by trying to split the input name into first and last name

    first_name = name.split(" ")[0]
    last_name = name.split(" ")[-1]

    # next, we'll try the frist name, last name, and full affiliation, under the assumption that these are correct
    # the api fields for these are "given-names", "family-name", and "affiliation-org-name"

    search_url = f"https://pub.orcid.org/v3.0/search?q=given-names:{first_name}+AND+family-name:{last_name}+AND+affiliation-org-name:{institution}&rows=10"
    headers = {
        "Accept": "application/json"
    }
    response = requests.get(search_url, headers=headers)
    
    if response.status_code == 200:
        search_results = response.json()

        # check to ensure that one and only one result was returned
        if search_results.get("num-found") == 1:
            return search_results.get("result")[0].get("orcid-identifier").get("uri")
    
    # if this fails, we'll check if the affiliation is an acronym, and try again
    # we'll do this by searching without the affiliation, and then iterating through the results
    # to see if the sequence of capital letters in the listed affiliations matches the acronym
    # if it does, we'll return that ORCID
    # if it doesn't, we'll return None
    search_url = f"https://pub.orcid.org/v3.0/search?q=given-names:{first_name}+AND+family-name:{last_name}&rows=10"
    headers = {
        "Accept": "application/json"
    }
    response = requests.get(search_url, headers=headers)
    
    if response.status_code == 200:
        search_results = response.json()

        # unfortunately, we don't have any efficient way to narrow these down, so we just have to start iterating through them
        # TODO: if there are any more advanced ways to compare the input affiliation and the returned affiliations, this
        # would be a good place to implement them.  For example, fuzzy matching, synonomous organizations, cap insensitive, etc.
        
        for result in search_results.get("result"):
            # if the result returns too many results, we'll throw a warning indicaitng this and return none
            if search_results.get("num-found") > 10:
                print(f"Warning: {search_results.get('num-found')} results found for {first_name} {last_name} without affiliation. Returning None.")
                return None
            # we include a wait here to avoid overloading the API
            time.sleep(0.5)
            # check to see if the affiliation is an acronym
            # first get the ORCID
            orcid = result.get("orcid-identifier").get("path")
            # then use this to query the ORCID API for the full record
            record_url = f"https://pub.orcid.org/v3.0/{orcid}"
            headers = {
                "Accept": "application/json"
            }

            record_response = requests.get(record_url, headers=headers)

            if record_response.status_code == 200:

                # get the affiliations from the record
                affiliations = record_response.json().get("activities-summary").get("employments").get("employment-summary")
                # iterate through the affiliations
                for iAffiliation in affiliations:
                    # For each affiliation, only check the capitalized letters
                    # This is because the API returns the full affiliation, which may include non-capitalized words
                    # We only want to check the capitalized words, as these are the ones that are likely to be acronyms
                    capitalized_affiliation = "".join([char for char in iAffiliation.get("organization").get("name") if char.isupper()])
                    # check to see if the capitalized letters match the affiliation
                    if capitalized_affiliation == institution or iAffiliation == institution:
                        # if they do, then this is likely our best guess as to the match, so return the full ORCID
                        # be sure to format the ORCID correctly, orcid is currently just 'path'
                        return f"https://orcid.org/{orcid}"
    # if we can't find anything for either a direct match to the name + affiliation, or the name + affiliation abbreviation, we
    # we should simply return none    
    return None

def search_github_user_for_orcid_robust(username):
    """
    This function searches for an ORCID link in a GitHub user's profile.
    If it fails to find one, it then searches the ORCID database for an individual
    whose name matches the account, and whose affiliation matches the account's institution.
    If it fails to find one in either of these cases, it returns None.

    Parameters
    ----------
    username : str
        The GitHub username to search for.
    
    Returns
    -------
    ORCID : str
        The ORCID link, if found, or None if not found.
    """
    
    # first use the GitHub API to verify that the user exists
    import requests
    user_url = f"https://api.github.com/users/{username}"
    response = requests.get(user_url)
    # if the user does not exist, throw an error
    if response.status_code != 200:
        raise ValueError(f"GitHub user {username} does not exist.")
    
    # if the user does exist, then we can proceed with the search
    # first, we'll search the user's profile for an ORCID link
    orcid_links = search_github_user_for_orcid(username)
    # if the result is not None, and a list, then we'll return the first result
    # if is not None and a string, then we'll return the result
    if orcid_links is not None:
        if type(orcid_links) == list:
            return orcid_links[0]
        elif type(orcid_links) == str:
            return orcid_links

    # if we don't find one, then we'll try to search the ORCID database for a match
    # first, we'll get the user's name and affiliation
    [name, affiliation] = get_name_and_institution_from_github_api(username)
    # then we'll search the ORCID database for a match
    orcid_link = search_orcid_individual(name, affiliation)
    # if we find one, return it
    if orcid_link is not None:
        return orcid_link
    # TODO: this is where we would add other search methods, such as scraping the HTML of the personal website list on GitHub
    return None

# here we test the search_github_user_for_orcid_robust function
# using francopestilli, who does not (as of 05/18/2023) have an ORCID listed in their GitHub profile
# though his ORCID is known to be https://orcid.org/0000-0002-2469-0494
# here we define the test function

def test_search_github_user_for_orcid_robust():
    """
    This function tests the search_github_user_for_orcid_robust function.
    It does so by searching for the ORCID of francopestilli, who does not
    have an ORCID listed in their GitHub profile.
    """

    # first we'll import the function
    # maybe not necessary if we're running this in the same file?
    # from github_profile_orcid_search import search_github_user_for_orcid_robust
    # then we'll search for the ORCID
    orcid_link = search_github_user_for_orcid_robust("francopestilli")
    # then we'll assert that the ORCID is correct
    assert orcid_link == "https://orcid.org/0000-0002-2469-0494"





import unittest
from unittest.mock import patch


class TestGithubProfileOrcidSearch(unittest.TestCase):

       def test_search_github_user_for_orcid(self):
        # Perform the search
        username = "DanNBullock"
        orcid_links = search_github_user_for_orcid(username)

        # Assert the ORCID link is found
        expected_orcid_link = "https://orcid.org/0000-0002-4321-2180"
        self.assertIn(expected_orcid_link, orcid_links)    

if __name__ == '__main__':
    unittest.main()



# the cffinit code found at https://github.com/citation-file-format/cff-initializer-javascript uses a javascript framework
# which we must here adapt to python.
def generateCFFFile(repositoryDetails):
    """
