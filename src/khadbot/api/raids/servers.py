"""
api/raids/servers.py

``GET /v1/servers``: every US and EU server a guild or character search can name, from
``config/registries/servers.yaml`` (``wcl.search.load_server_list``). ``create_app`` loads the file and renders the
response once at start-up (``server_list_response``; a malformed file stops the API). One response with no search
parameter, a day's ``Cache-Control`` and an ETag of the file's content (304 on ``If-None-Match``); it never asks WCL,
and the web app filters the list in the browser as the user types. The searches check a region and server against the
same list (``check_server``) and answer 404 ``server_unknown`` without spending a point.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response

from khadbot.api.schemas import (
    ServerEntry,
    ServerList,
    ServerRegion,
)
from khadbot.api.views import ApiError
from khadbot.wcl.search import (
    DEFAULT_SERVER_LIST_PATH,
    GuildSource,
    Region,
    load_server_list,
    match_key,
)


def get_server_list(request: Request) -> tuple[Region, ...]:
    """The server list ``create_app`` loaded at start-up."""
    regions: tuple[Region, ...] = request.app.state.server_list
    return regions


SERVER_LIST_MAX_AGE_S = 86400  # a browser keeps the list a day, then revalidates it with the ETag


def server_list_response(path: Path | str = DEFAULT_SERVER_LIST_PATH) -> tuple[tuple[Region, ...], bytes, str]:
    """
    The server list file loaded (``load_server_list``), the ``GET /v1/servers`` body built from it once, and its ETag:
    a hash of the file's bytes, so it changes exactly when the file is rebuilt.

    Raises:
        ConfigurationError: The file is missing or malformed.
    """
    regions = load_server_list(path)
    body = (
        ServerList(
            regions=[
                ServerRegion(
                    slug=r.slug,
                    name=r.compact_name,
                    servers=[
                        ServerEntry(name=s.name, slug=s.slug, region=r.slug, match=match_key(s.name)) for s in r.servers
                    ],
                    complete=r.complete,
                )
                for r in regions
            ]
        )
        .model_dump_json()
        .encode()
    )
    etag = '"' + hashlib.sha256(Path(path).read_bytes()).hexdigest()[:32] + '"'
    return regions, body, etag


def check_server(regions: tuple[Region, ...] | list[Region], source: GuildSource) -> None:
    """
    Raises:
        ApiError: 404 ``server_unknown`` when the region isn't offered or the server isn't in its list.
    """
    region = next((r for r in regions if r.slug == source.region), None)
    if region is None or all(s.slug != source.server for s in region.servers):
        raise ApiError(404, "server_unknown", "No such server in the United States or Europe.")


router = APIRouter(tags=["search"])


@router.get(
    "/v1/servers",
    response_model=ServerList,
    responses={304: {"description": "Not modified: the list matches the If-None-Match ETag"}},
)
async def list_servers(request: Request) -> Any:
    """
    Every server a guild or character search can name, by region (United States, then Europe), in one response: no
    search parameter, no filtering. Filter it in the browser as the user types (``match``). It changes only when the
    file is rebuilt: cache it for a day and revalidate with its ETag.
    """
    etag: str = request.app.state.server_list_etag
    headers = {"ETag": etag, "Cache-Control": f"public, max-age={SERVER_LIST_MAX_AGE_S}"}
    if etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(content=request.app.state.server_list_body, media_type="application/json", headers=headers)


__all__ = [
    "SERVER_LIST_MAX_AGE_S",
    "check_server",
    "get_server_list",
    "router",
    "server_list_response",
]
