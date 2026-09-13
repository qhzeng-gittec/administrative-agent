"""Deployed enterprise runtime: SQLite and model APIs, without school RAG services."""
from types import SimpleNamespace
from app.auth import AuthService
from app.enterprise.service import EnterpriseService
from app.utils.ratelimit import MemoryRateLimiter


class AccountStore:
    def __init__(self, store):
        self.store = store

    async def get(self, collection, key):
        return self.store.get(collection, key)

    async def upsert_user(self, user):
        self.store.put("users", {**user, "id": user["_id"]})

    async def find(self, collection):
        return self.store.records(collection)


async def build_runtime(settings):
    enterprise = EnterpriseService(settings)
    accounts = AccountStore(enterprise.store)
    auth = AuthService(accounts, settings)
    if settings.seed_demo_users:
        for user in ({"username": "employee", "password": "employee123", "name": "团队成员", "role": "employee"},
                     {"username": "admin", "password": "admin123", "name": "系统管理员", "role": "admin"}):
            if await accounts.get("users", user["username"]) is None:
                await accounts.upsert_user(auth._to_user(user))
    return SimpleNamespace(settings=settings, enterprise=enterprise, store=accounts, auth=auth,
                           login_limiter=MemoryRateLimiter())
