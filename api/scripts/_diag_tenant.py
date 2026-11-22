import asyncio

from api.db import db_client


async def main():
    u = await db_client.get_user_by_id(4)
    print("user4 selected =", u.selected_organization_id)
    w = await db_client.get_workflow(1, organization_id=2)
    print("wf1@org2 =", w)
    w2 = await db_client.get_workflow(1, organization_id=1)
    print("wf1@org1 exists =", w2 is not None)
    u1 = await db_client.get_user_by_id(1)
    print("user1 selected =", u1.selected_organization_id)


asyncio.run(main())
