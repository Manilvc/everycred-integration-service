"""Route that resolves stored catalogue ids for a client.

A client's backend stores type, tool, and field ids as references and
calls this with its ``X-API-Key`` to get their names for display.
"""

from fastapi import APIRouter, Depends, status

from app.features.catalog.dependencies import CatalogServiceDep
from app.features.catalog.schemas import (
    CatalogResolveRequest,
    CatalogResolveResponse,
)
from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.shared.schemas import ErrorResponse

router = APIRouter(
    prefix="/client/catalog",
    tags=["Client Catalog"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing or invalid API key",
        },
    },
)


# POST rather than GET: a page of records can carry hundreds of ids,
# which does not fit reliably in a query string.
@router.post(
    "/resolve",
    response_model=CatalogResolveResponse,
    summary="Resolve type, tool, and field ids to display data",
)
async def resolve_catalog_ids(
    payload: CatalogResolveRequest,
    current_client: CurrentClient,
    service: CatalogServiceDep,
) -> CatalogResolveResponse:
    """Return names and keys for stored type, tool, and field ids.

    Send every id a page needs in one call. Each kind accepts up to 500
    ids; duplicates are fine. Ids that match nothing visible to the
    client, such as a removed field, come back in `not_found` rather
    than failing the request.
    """
    return await service.resolve(current_client, payload)
