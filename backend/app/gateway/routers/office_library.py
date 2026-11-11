"""Office library HTTP boundary: authentication context and bounded transport."""
import asyncio
import base64

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deerflow.office.library_adapter import library_for_user
from deerflow.runtime.user_context import get_effective_user_id
from office_library.service import DuplicateFile
from office_library.providers import ProviderError

router = APIRouter(prefix='/api/office/library', tags=['office'])


class Upload(BaseModel):
    filename: str = Field(max_length=180)
    content: str = Field(max_length=28_000_000)
    replace_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    expected_version: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class Settings(BaseModel):
    embedding_url: str = Field(max_length=2048)
    embedding_model: str = Field(max_length=200)
    rerank_url: str = Field(max_length=2048)
    rerank_model: str = Field(max_length=200)
    embedding_api_key: str = Field(default='', max_length=4096)
    rerank_api_key: str = Field(default='', max_length=4096)


async def call(method, *args):
    service = await asyncio.to_thread(library_for_user, get_effective_user_id())
    try:
        return await asyncio.to_thread(getattr(service, method), *args)
    except DuplicateFile as error:
        raise HTTPException(409, {'code': 'duplicate', 'document': error.document}) from None
    except FileNotFoundError:
        raise HTTPException(404, {'code': 'errors.library_missing', 'params': {}}) from None
    except ValueError as error:
        raise HTTPException(400, {'code': error.public_code, 'params': error.public_params} if getattr(error, 'public_code', None) else str(error)) from None
    except ProviderError as error:
        raise HTTPException(503, {'code': error.public_code, 'params': error.public_params} if getattr(error, 'public_code', None) else str(error)) from None
    except Exception:
        raise HTTPException(503, {'code': 'errors.library_failed', 'params': {}}) from None


@router.get('/documents')
async def documents():
    return {'documents': await call('list_documents')}


@router.post('/documents')
async def upload(body: Upload):
    try:
        content = base64.b64decode(body.content, validate=True)
    except ValueError:
        raise HTTPException(400, {'code': 'errors.invalid_encoding', 'params': {}}) from None
    return await call('upload', body.filename, content, body.replace_id, body.expected_version)


@router.get('/documents/{document_id}/file')
async def read(document_id: str, version: str | None = None, part: int | None = None):
    name, content = await call('read_file', document_id, version)
    location = await call('locate', document_id, version, part) if version and part is not None else {}
    return {'filename': name, 'content': base64.b64encode(content).decode(), **location}


@router.delete('/documents/{document_id}')
async def delete(document_id: str):
    await call('delete', document_id)
    return {'ok': True}


@router.post('/documents/{document_id}/retry')
async def retry(document_id: str):
    await call('retry', document_id)
    return {'ok': True}


@router.get('/settings')
async def settings():
    return await call('settings')


@router.put('/settings')
async def save_settings(body: Settings):
    return await call('save_settings', body.model_dump())


@router.post('/test-connection')
async def test_connection():
    return await call('test_connection')
