"""Admin API composition — everything under /admin/v1, bearer-token gated.
The Chatwoot Rails proxy is the only intended caller; it enforces Chatwoot
roles (admin vs agent allowlist) before requests ever reach this service."""

from fastapi import APIRouter, Depends

from app.api.admin import config_api, ops_api, selfservice_api, testing_api
from app.security import require_admin

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(require_admin)])
router.include_router(config_api.router)
router.include_router(ops_api.router)
router.include_router(testing_api.router)
router.include_router(selfservice_api.router)
