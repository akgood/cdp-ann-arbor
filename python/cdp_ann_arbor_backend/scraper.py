#!/usr/bin/env python
# -*- coding: utf-8 -*-

from datetime import datetime

import logging
import json
from typing import Dict, List, Optional
from urllib.request import urlopen
from urllib.parse import urlparse
from pathlib import Path

from cdp_backend.pipeline.ingestion_models import (
    Person,
    EventIngestionModel,
    MinutesItem,
)

from cdp_scrapers.legistar_utils import (
    LegistarScraper,
    LEGISTAR_EV_INDEX,
    LEGISTAR_EV_VOTES,
    LEGISTAR_MINUTE_NAME,
    LEGISTAR_EV_MINUTE_DECISION,
    LEGISTAR_MINUTE_EXT_ID,
    LEGISTAR_VOTE_VAL_NAME,
    LEGISTAR_VOTE_VAL_ID,
    VoteDecision,
    EventMinutesItem,
    EventMinutesItemDecision,
    LEGISTAR_EV_ATTACHMENTS,
    Vote,
    LEGISTAR_VOTE_EXT_ID,
    LEGISTAR_VOTE_PERSONS,
    LEGISTAR_MATTER_STATUS,
    MatterStatusDecision
)
from cdp_scrapers.types import ContentURIs
from cdp_scrapers.scraper_utils import str_simplified, reduced_list

###############################################################################

log = logging.getLogger(__name__)

###############################################################################

STATIC_FILE_KEY_PERSONS = "persons"
STATIC_FILE_DEFAULT_PATH = Path(__file__).parent / "annarbor-static.json"

known_persons: Optional[Dict[str, Person]] = None

# load long-term static data at file load-time
if Path(STATIC_FILE_DEFAULT_PATH).exists():
    with open(STATIC_FILE_DEFAULT_PATH, "rb") as json_file:
        static_data = json.load(json_file)

    known_persons = {}
    for name, person in static_data[STATIC_FILE_KEY_PERSONS].items():
        known_persons[name] = Person.from_dict(person)


if known_persons:
    log.debug(f"loaded static data for {', '.join(known_persons.keys())}")

# PERSON_ALIASES = {"Dan Strauss": set(["Daniel Strauss"])}
PERSON_ALIASES = {}

###############################################################################


class AnnArborScraper(LegistarScraper):
    PYTHON_MUNICIPALITY_SLUG: str = "annarbor"

    def __init__(self):
        """
        A2 specific implementation of LegistarScraper.
        """
        super().__init__(
            client="a2gov",
            timezone="America/Detroit",
            ignore_minutes_item_patterns=[],
            known_persons=known_persons,
            person_aliases=PERSON_ALIASES,
            vote_approve_pattern="approve|favor|yes|yea",
            vote_reject_pattern="reject|oppose|no|nay",
            matter_in_progress_pattern=r"heard|read|filed|held|(?:in.*com+it+ee)|lay on table",
            matter_rejected_pattern=r"rejected|dropped|defeated",
        )

    def get_minutes_item(self, legistar_ev_item: Dict) -> Optional[MinutesItem]:
        """
        Return MinutesItem from parts of Legistar API EventItem.
        Parameters
        ----------
        legistar_ev_item: Dict
            Legistar API EventItem
        Returns
        -------
        minutes_item: Optional[MinutesItem]
            None if could not get nonempty MinutesItem.name from EventItem.
        """

        action_to_present_tense_map = {
            "Approved": "",
            "Postponed": "Postpone",
            "Referred": "Refer"
        }

        action = str_simplified(legistar_ev_item["EventItemActionName"])
        normalized_action = action_to_present_tense_map.get(action, action)

        minutes_item_name_parts = [
            normalized_action,
            str_simplified(legistar_ev_item["EventItemAgendaNumber"]),
            str_simplified(legistar_ev_item[LEGISTAR_MINUTE_NAME]),
        ]

        return self.get_none_if_empty(
            MinutesItem(
                external_source_id=str(legistar_ev_item[LEGISTAR_MINUTE_EXT_ID]),
                name=": ".join(p for p in minutes_item_name_parts if p),
            )
        )

    def fix_event_minutes(
        self, ev_minutes_item: Optional[EventMinutesItem], legistar_ev_item: Dict
    ) -> Optional[EventMinutesItem]:
        """
        Inspect the MinutesItem and Matter in ev_minutes_item.
        - Move some fields between them to make the information more meaningful.
        - Enforce matter.result_status when appropriate.

        Parameters
        ----------
        ev_minutes_item: Optional[EventMinutesItem]
            The specific event minutes item to clean.
            Or None if running this function in a loop with multiple event minutes
            items and you don't want to clean / the emi was filtered out.
        legistar_ev_item: Dict
            The original Legistar EventItem.

        Returns
        -------
        cleaned_emi: Optional[EventMinutesItem]
            The cleaned event minutes item. This can clean both the event minutes item
            and the attached matter information.
        """
        if not ev_minutes_item:
            return ev_minutes_item
        # XXX skip the following
        # if ev_minutes_item.minutes_item and ev_minutes_item.matter:
        #     # we have both matter and minutes_item
        #     # - make minutes_item.name the more concise text e.g. "CB 11111"
        #     # - make minutes_item.description the more descriptive lengthy text
        #     #   e.g. "AN ORDINANCE related to the..."
        #     # - make matter.title the same descriptive lengthy text
        #     ev_minutes_item.minutes_item.description = ev_minutes_item.minutes_item.name
        #     ev_minutes_item.minutes_item.name = ev_minutes_item.matter.name
        #     ev_minutes_item.matter.title = ev_minutes_item.minutes_item.description

        # matter.result_status is allowed to be null
        # only when no votes or Legistar EventItemMatterStatus is null
        if ev_minutes_item.matter and not ev_minutes_item.matter.result_status:
            if ev_minutes_item.votes and legistar_ev_item[LEGISTAR_MATTER_STATUS]:
                # means did not find matter_*_pattern in Legistar EventItemMatterStatus.
                # default to in progress (as opposed to adopted or rejected)
                # NOTE: if our matter_*_patterns ARE "complete",
                #       this clause would hit only because the info from Legistar
                #       is incomplete or malformed
                ev_minutes_item.matter.result_status = MatterStatusDecision.IN_PROGRESS

        return ev_minutes_item

    def get_votes(
        self, legistar_votes: List[Dict], minutes_item_decision: Optional[str]
    ) -> Optional[List[Vote]]:
        """
        Override parent class to pass in minutes_item_decision
        """

        votes = reduced_list(
            [
                self.get_none_if_empty(
                    Vote(
                        decision=self.get_vote_decision(vote, minutes_item_decision),
                        external_source_id=str(vote[LEGISTAR_VOTE_EXT_ID]),
                        person=self.get_person(vote[LEGISTAR_VOTE_PERSONS]),
                    )
                )
                for vote in legistar_votes
            ]
        )
        ###asdf
        logging.debug("votes: {}".format(votes))
        return votes

    def get_vote_decision(
        self, legistar_vote: Dict, minutes_item_decision: Optional[str]
    ) -> Optional[str]:
        """
        In Ann Arbor, many votes are taken on "voice vote" rather than "roll call",
        in which case individual CM votes aren't recorded / show up as "null". (It
        appears that absent CMs still have an "absent" vote recorded in these cases).

        This procedure is usually reserved for unanimous actions, so we'll assume
        in these cases that all "null" votes are consistent with the overall outcome.
        """
        if (
            legistar_vote[LEGISTAR_VOTE_VAL_NAME] is None
            and legistar_vote[LEGISTAR_VOTE_VAL_ID] is None
        ):
            if minutes_item_decision == EventMinutesItemDecision.PASSED:
                return VoteDecision.APPROVE
            elif minutes_item_decision == EventMinutesItemDecision.FAILED:
                return VoteDecision.REJECT

        return super().get_vote_decision(legistar_vote)

    def get_event_minutes(
        self, legistar_ev_items: List[Dict]
    ) -> Optional[List[EventMinutesItem]]:
        """
        Override parent class to pass the minutes_item_decision into get_votes()
        """
        return reduced_list(
            [
                self.get_none_if_empty(
                    self.fix_event_minutes(
                        # if minutes_item contains unimportant data,
                        # just make the entire EventMinutesItem = None
                        self.filter_event_minutes(
                            EventMinutesItem(
                                index=item[LEGISTAR_EV_INDEX],
                                minutes_item=self.get_minutes_item(item),
                                votes=self.get_votes(
                                    item[LEGISTAR_EV_VOTES],
                                    self.get_minutes_item_decision(
                                        item[LEGISTAR_EV_MINUTE_DECISION]
                                    ),
                                ),
                                matter=self.get_matter(item),
                                decision=self.get_minutes_item_decision(
                                    item[LEGISTAR_EV_MINUTE_DECISION]
                                ),
                                supporting_files=self.get_event_supporting_files(
                                    item[LEGISTAR_EV_ATTACHMENTS]
                                ),
                            )
                        ),
                        item,
                    )
                )
                # EventMinutesItem object per member in EventItems
                for item in legistar_ev_items
            ]
        )

    def get_content_uris(self, legistar_ev: Dict) -> List[ContentURIs]:
        """
        Return URLs for videos and captions parsed from seattlechannel.org web page

        Parameters
        ----------
        legistar_ev: Dict
            Data for one Legistar Event.

        Returns
        -------
        content_uris: List[ContentURIs]
            List of ContentURIs objects for each session found.

        See Also
        --------
        parse_content_uris()

        Notes
        -----
        get_events() calls get_content_uris() to get video and caption URIs.
        get_content_uris() gets video page URL from EventInSiteURL.
        If "videoid" in video page URL, calls parse_content_uris().
        Else, calls get_video_page_urls() to get proper video page URL with "videoid",
            then calls parse_content_uris().
        get_events()
            -> get_content_uris()
                -> parse_content_uris()
                or
                -> get_video_page_urls(), parse_content_uris()
        """

        media_url = legistar_ev.get("EventMedia")
        if not media_url:
            # TODO: scrape CTN directly and try to pattern-match
            log.debug("No media url in Legistar info")
            return []

        try:
            parsed_media_url = urlparse(media_url)
            show_id = parsed_media_url.path.split("/")[-1]
            show_info_uri = (
                f"https://reflect-ctn.cablecast.tv/CablecastAPI/v1/shows/{show_id}"
            )
        except Exception:
            log.debug("Failed to parse media_url")

        try:
            with urlopen(show_info_uri) as resp:
                show_info = json.loads(resp.read())
        except Exception:
            log.debug(f"Failed to open {show_info_uri}")
            return []

        try:
            (vod_id,) = show_info["show"]["vods"]
        except Exception:
            log.debug("No vod info found")
            return []

        vod_info_url = f"https://reflect-ctn.cablecast.tv/CablecastAPI/v1/vods/{vod_id}"
        try:
            with urlopen(vod_info_url) as resp:
                vod_info = json.loads(resp.read())
        except Exception:
            log.debug(f"Failed to open {vod_info_url}")
            return []

        try:
            vod_url = vod_info["vod"]["url"]
            vod_caption_url = "{}/captions.vtt".format(
                vod_url.rsplit("/", maxsplit=1)[0]
            )
        except KeyError:
            log.debug("Malformed vod info")
            return []

        return [ContentURIs(vod_url, vod_caption_url)]


def get_events(
    from_dt: datetime,
    to_dt: datetime,
    **kwargs,
) -> List[EventIngestionModel]:
    """
    Get all events for the provided timespan.

    Parameters
    ----------
    from_dt: datetime
        Datetime to start event gather from.
    to_dt: datetime
        Datetime to end event gather at.

    Returns
    -------
    events: List[EventIngestionModel]
        All events gathered that occured in the provided time range.

    Notes
    -----
    As the implimenter of the get_events function, you can choose to ignore the from_dt
    and to_dt parameters. However, they are useful for manually kicking off pipelines
    from GitHub Actions UI.
    """

    # Your implementation here
    return AnnArborScraper().get_events(from_dt, to_dt, **kwargs)
