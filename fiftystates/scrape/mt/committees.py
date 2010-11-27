import lxml.html
import urllib
from lxml.etree import ElementTree

from fiftystates.scrape import NoDataForPeriod
from fiftystates.scrape.committees import CommitteeScraper, Committee
from fiftystates.scrape.mt import metadata

class MTCommitteeScraper(CommitteeScraper):
    state = 'mt'
    committee_list_url_template = "http://laws.leg.mt.gov/laws%s/law0240w$cmte.startup"
    committee_url_template = "http://laws.leg.mt.gov/laws%s/LAW0240W$CMTE.ActionQuery?P_COM_NM=%s&Z_ACTION=Find&P_ACTN_DTM=&U_ACTN_DTM="

    def scrape(self, chamber, term_name):
        if int(term_name) < 56:
            raise NoDataForPeriod
        
        term = None

        for t in metadata['terms']:
            if t['name'] == term_name:
                term = t

        laws_year = term['sessions'][0][2:]
        committees = self.get_committees(term, chamber, laws_year)
        for committee in committees:
            committee = self.add_committee_members(committee)
            self.save_committee(committee)

    def get_committees(self, term, chamber, laws_year):
        committee_list = []

        committee_list_url = self.committee_list_url_template % laws_year
        list_page = ElementTree(lxml.html.fromstring(self.urlopen(committee_list_url)))
        com_select = list_page.find('//select[@name="P_COM_NM"]')

        for option in com_select.findall("option"):
            if option.text:
                committee_url = self.committee_url_template % (laws_year,
                                                               urllib.quote(option.text.strip()))
                c_chamber, name = option.text.split(" ", 1)
                c_chamber = c_chamber[1]
                if (('H' == c_chamber and 'lower' == chamber) or
                   ('S' == c_chamber and 'upper' == chamber)):
                    # committee = Committee(term['name'], chamber, name)
                    committee = Committee(chamber, name)
                    committee.add_source(committee_url)
                    committee_list.append(committee)
        return committee_list

    def add_committee_members(self, committee):
        url = committee['sources'][0]['url']
        self.logger.info("parsing %s from %s" % (committee['committee'], url))
        details = ElementTree(lxml.html.fromstring(self.urlopen(url)))
        for table in details.findall("//table"):
            headers = table.findall("tr/th")
            if (len(headers) == 2 and
                headers[0].text == 'Member' and
                headers[1].text == 'Assignment'):
                for row in table.findall("tr")[1:]:
                    name_cell, role_cell = row.findall("td")
                    name = name_cell.text_content()
                    role = role_cell.text_content().lower()
                    committee.add_member(name, role)
                    self.logger.debug("Added %s (%s)" % (name, role))
        self.logger.info("Found %d members of the %s committee" % (len(committee['members']),
                                                                   committee['committee']))
        return committee
