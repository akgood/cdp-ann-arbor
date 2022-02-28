#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import json
from typing import Dict, List, Optional
from urllib.request import urlopen
from urllib.parse import urlparse
from pathlib import Path

from cdp_backend.pipeline.ingestion_models import Person

from cdp_scrapers.legistar_utils import (
    LegistarScraper,
)
from cdp_scrapers.types import ContentURIs

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
