import logging
from json import JSONDecodeError

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.exceptions import HTTPException
from voluptuous import Invalid

from db.test_user import TestUser
from middleware.authenticated import User

logger = logging.getLogger(__name__)


async def create_test_user(request: Request):
    test_user_db: TestUser = request.app.state.db.test_user

    try:
        body = await request.json()
    except JSONDecodeError:
        raise HTTPException(status_code=400, detail="Could not parse request body to JSON.")

    try:
        await test_user_db.upsert({
            'first_name': body.get('first_name'),
            'last_name': body.get('last_name'),
            'emails': body.get('emails'),
        })
    except Invalid as e:
        raise HTTPException(status_code=400, detail=f"Invalid test user payload: {e}")

    return Response(status_code=201)


async def get_test_users(request: Request):
    user: User = request.user
    if not user.is_admin:
        raise HTTPException(status_code=403, detail='Admin role required.')

    test_user_db: TestUser = request.app.state.db.test_user
    return JSONResponse(content=await test_user_db.all())


async def delete_test_user(request: Request):
    user: User = request.user
    if not user.is_admin:
        raise HTTPException(status_code=403, detail='Admin role required.')

    first_name = request.query_params.get('first_name')
    last_name = request.query_params.get('last_name')
    if not first_name or not last_name:
        raise HTTPException(
            status_code=400,
            detail='first_name and last_name query parameters are required.',
        )

    test_user_db: TestUser = request.app.state.db.test_user
    result = await test_user_db.collection.delete_one(
        {'first_name': first_name, 'last_name': last_name}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail='Test user not found.')

    return Response(status_code=204)
