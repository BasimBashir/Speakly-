"""API routes for knowledge base operations."""

import os
import tempfile
import uuid
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger

from api.db import db_client
from api.db.models import KnowledgeBaseDocumentModel
from api.enums import PostHogEvent
from api.schemas.knowledge_base import (
    ChunkSearchRequestSchema,
    ChunkSearchResponseSchema,
    DescribePreviewResponseSchema,
    DocumentContentResponseSchema,
    DocumentContentUpdateRequestSchema,
    DocumentListResponseSchema,
    DocumentResponseSchema,
    DocumentUploadRequestSchema,
    DocumentUploadResponseSchema,
    EditDocumentRequestSchema,
    ProcessDocumentRequestSchema,
)
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.knowledge_base.describe_preview import (
    DescribePreviewError,
    generate_description_preview,
)
from api.services.knowledge_base_content import (
    DocumentContentConflictError,
    DocumentContentTooLargeError,
    DocumentNotEditableError,
    enqueue_document_processing,
    read_document_content,
    update_document_content,
)
from api.services.posthog_tenant import capture_tenant_event
from api.services.storage import storage_fs
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


def _has_live_content(document: KnowledgeBaseDocumentModel) -> bool:
    # Processing only replaces full_text / chunks on success, so these reflect
    # the version agents retrieve even while a re-index is running or failed.
    if document.retrieval_mode == "full_document":
        # Retrieval skips empty text, so an empty document serves nothing.
        return bool(document.full_text)
    return document.total_chunks > 0


def _to_document_response(
    document: KnowledgeBaseDocumentModel,
) -> DocumentResponseSchema:
    return DocumentResponseSchema(
        id=document.id,
        document_uuid=document.document_uuid,
        filename=document.filename,
        file_size_bytes=document.file_size_bytes,
        file_hash=document.file_hash,
        mime_type=document.mime_type,
        processing_status=document.processing_status,
        processing_error=document.processing_error,
        total_chunks=document.total_chunks,
        retrieval_mode=document.retrieval_mode,
        custom_metadata=document.custom_metadata,
        docling_metadata=document.docling_metadata,
        source_url=document.source_url,
        created_at=document.created_at,
        updated_at=document.updated_at,
        organization_id=document.organization_id,
        created_by=document.created_by,
        is_active=document.is_active,
        has_live_content=_has_live_content(document),
        doc_type=document.doc_type,
        intended_use=document.intended_use or [],
        user_description=document.user_description,
        doc_card=document.doc_card,
        doc_card_extracted_at=document.doc_card_extracted_at,
        topics=document.topics or [],
    )


@router.post(
    "/upload-url",
    response_model=DocumentUploadResponseSchema,
    summary="Get presigned URL for document upload",
)
async def get_upload_url(
    request: DocumentUploadRequestSchema,
    user=Depends(get_user),
):
    """Generate a presigned PUT URL for uploading a document.

    This endpoint:
    1. Generates a unique document UUID for organizing the S3 key
    2. Generates a presigned S3/MinIO URL for uploading the file
    3. Returns the upload URL and document metadata

    After uploading to the returned URL, call /process-document to create
    the document record and trigger processing.

    Access Control:
    * All authenticated users can upload documents scoped to their organization.
    """

    try:
        # Generate unique document UUID for S3 organization
        document_uuid = str(uuid.uuid4())

        # Generate S3 key: knowledge_base/{org_id}/{document_uuid}/{filename}
        s3_key = f"knowledge_base/{user.selected_organization_id}/{document_uuid}/{request.filename}"

        # Generate presigned PUT URL (valid for 30 minutes)
        upload_url = await storage_fs.aget_presigned_put_url(
            file_path=s3_key,
            expiration=1800,  # 30 minutes
            content_type=request.mime_type,
            max_size=100_000_000,  # 100MB max
        )

        if not upload_url:
            raise HTTPException(
                status_code=500, detail="Failed to generate presigned upload URL"
            )

        logger.info(
            f"Generated upload URL for document {document_uuid}, "
            f"user {user.id}, org {user.selected_organization_id}"
        )

        return DocumentUploadResponseSchema(
            upload_url=upload_url,
            document_uuid=document_uuid,
            s3_key=s3_key,
        )

    except Exception as exc:
        logger.error(f"Error generating upload URL: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to generate upload URL"
        ) from exc


@router.post(
    "/process-document",
    response_model=DocumentResponseSchema,
    summary="Trigger document processing",
)
async def process_document(
    request: ProcessDocumentRequestSchema,
    user=Depends(get_user),
):
    """Trigger asynchronous processing of an uploaded document.

    This endpoint should be called after successfully uploading a file to the presigned URL.
    It will:
    1. Create a document record in the database with the specified UUID
    2. Enqueue a background task to process the document (chunking and embedding)

    The document status will be updated from 'pending' -> 'processing' -> 'completed' or 'failed'.

    Embedding:
    Uses OpenAI text-embedding-3-small (1536-dimensional embeddings, requires API key configured in Model Configurations).

    Access Control:
    * Users can only process documents in their organization.
    """

    try:
        # Extract filename from s3_key
        filename = request.s3_key.split("/")[-1]

        # Create document record with the specific UUID from upload
        document = await db_client.create_document(
            organization_id=user.selected_organization_id,
            created_by=user.id,
            filename=filename,
            file_size_bytes=0,
            file_hash="",
            mime_type="application/octet-stream",
            custom_metadata={"s3_key": request.s3_key},
            document_uuid=request.document_uuid,
            retrieval_mode=request.retrieval_mode,
            doc_type=request.doc_type,
            intended_use=request.intended_use,
            user_description=request.user_description,
        )

        # Enqueue background task for processing
        await enqueue_document_processing(
            document_id=document.id,
            s3_key=request.s3_key,
            organization_id=user.selected_organization_id,
            created_by_provider_id=str(user.provider_id),
            retrieval_mode=request.retrieval_mode,
        )

        logger.info(
            f"Created document {request.document_uuid} (id={document.id}) and enqueued processing "
            f"with OpenAI embeddings, org {user.selected_organization_id}"
        )

        await capture_tenant_event(
            distinct_id=str(user.provider_id),
            event=PostHogEvent.KNOWLEDGE_BASE_CREATED,
            organization_id=user.selected_organization_id,
            properties={
                "document_uuid": str(request.document_uuid),
                "filename": filename,
                "retrieval_mode": request.retrieval_mode,
            },
        )

        return DocumentResponseSchema(
            id=document.id,
            document_uuid=request.document_uuid,
            filename=filename,
            file_size_bytes=0,
            file_hash="",
            mime_type="application/octet-stream",
            processing_status="pending",
            processing_error=None,
            total_chunks=0,
            retrieval_mode=request.retrieval_mode,
            custom_metadata={"s3_key": request.s3_key},
            docling_metadata={},
            source_url=None,
            created_at=document.created_at,
            updated_at=document.updated_at,
            organization_id=user.selected_organization_id,
            created_by=user.id,
            is_active=True,
            doc_type=request.doc_type,
            intended_use=request.intended_use,
            user_description=request.user_description,
            doc_card=None,
            doc_card_extracted_at=None,
            topics=[],
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error processing document: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to process document"
        ) from exc


@router.get(
    "/documents",
    response_model=DocumentListResponseSchema,
    summary="List documents",
    **sdk_expose(
        method="list_documents",
        description="List knowledge base documents available to the authenticated organization.",
    ),
)
async def list_documents(
    status: Annotated[
        Optional[str],
        Query(description="Filter by processing status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    user=Depends(get_user),
):
    """List all documents for the user's organization.

    Access Control:
    * Users can only see documents from their organization.
    """

    try:
        documents = await db_client.get_documents_for_organization(
            organization_id=user.selected_organization_id,
            processing_status=status,
            limit=limit,
            offset=offset,
        )

        document_list = [_to_document_response(doc) for doc in documents]

        return DocumentListResponseSchema(
            documents=document_list,
            total=len(document_list),
            limit=limit,
            offset=offset,
        )

    except Exception as exc:
        logger.error(f"Error listing documents: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list documents") from exc


@router.get(
    "/documents/{document_uuid}",
    response_model=DocumentResponseSchema,
    summary="Get document details",
)
async def get_document(
    document_uuid: str,
    user=Depends(get_user),
):
    """Get details of a specific document.

    Access Control:
    * Users can only access documents from their organization.
    """

    try:
        document = await db_client.get_document_by_uuid(
            document_uuid=document_uuid,
            organization_id=user.selected_organization_id,
        )

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        return _to_document_response(document)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting document: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get document") from exc


@router.get(
    "/documents/{document_uuid}/content",
    response_model=DocumentContentResponseSchema,
    summary="Get editable document content",
)
async def get_document_content(
    document_uuid: str,
    user=Depends(get_user),
):
    """Return the raw text of a .txt or .md document for editing.

    Access Control:
    * Users can only read documents from their organization.
    """

    document = await db_client.get_document_by_uuid(
        document_uuid=document_uuid,
        organization_id=user.selected_organization_id,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        content = await read_document_content(document)
    except DocumentNotEditableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error reading content of document {document_uuid}: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to load document content"
        ) from exc

    return DocumentContentResponseSchema(
        document_uuid=document.document_uuid,
        filename=document.filename,
        retrieval_mode=document.retrieval_mode,
        content=content,
        file_hash=document.file_hash,
    )


@router.put(
    "/documents/{document_uuid}/content",
    response_model=DocumentResponseSchema,
    summary="Replace document content and re-index",
)
async def save_document_content(
    document_uuid: str,
    request: DocumentContentUpdateRequestSchema,
    user=Depends(get_user),
):
    """Replace the text of a .txt or .md document and re-process it.

    The stored file is overwritten and the document is re-indexed in the
    background. Agents keep using the previous version until that succeeds.

    Access Control:
    * Users can only edit documents from their organization.
    """

    document = await db_client.get_document_by_uuid(
        document_uuid=document_uuid,
        organization_id=user.selected_organization_id,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        updated = await update_document_content(
            document,
            content=request.content,
            expected_file_hash=request.expected_file_hash,
            created_by_provider_id=str(user.provider_id),
        )
    except DocumentNotEditableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DocumentContentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DocumentContentTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error updating content of document {document_uuid}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to save document") from exc

    logger.info(
        f"Updated content of document {document_uuid} "
        f"(status={updated.processing_status}), user {user.id}, "
        f"org {user.selected_organization_id}"
    )

    await capture_tenant_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.KNOWLEDGE_BASE_UPDATED,
        organization_id=user.selected_organization_id,
        properties={
            "document_uuid": updated.document_uuid,
            "retrieval_mode": updated.retrieval_mode,
            "reprocessing": updated.processing_status == "pending",
        },
    )

    return _to_document_response(updated)


@router.delete(
    "/documents/{document_uuid}",
    summary="Delete document",
)
async def delete_document(
    document_uuid: str,
    user=Depends(get_user),
):
    """Soft delete a document and its chunks.

    Access Control:
    * Users can only delete documents from their organization.
    """

    try:
        success = await db_client.delete_document(
            document_uuid=document_uuid,
            organization_id=user.selected_organization_id,
        )

        if not success:
            raise HTTPException(status_code=404, detail="Document not found")

        logger.info(
            f"Deleted document {document_uuid}, "
            f"user {user.id}, org {user.selected_organization_id}"
        )

        return {"success": True, "message": "Document deleted successfully"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error deleting document: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to delete document"
        ) from exc


@router.patch(
    "/documents/{document_uuid}",
    response_model=DocumentResponseSchema,
    summary="Edit user-provided document fields",
)
async def edit_document_inputs(
    document_uuid: str,
    request: EditDocumentRequestSchema,
    user=Depends(get_user),
):
    """Edit user-provided fields on a document.

    Does NOT auto-trigger re-extraction. Use POST /documents/{uuid}/re-extract
    explicitly if you want the DocCard regenerated after editing.

    Access Control:
    * Users can only edit documents from their organization.
    """
    document = await db_client.update_document_user_inputs(
        document_uuid=document_uuid,
        organization_id=user.selected_organization_id,
        doc_type=request.doc_type,
        intended_use=request.intended_use,
        user_description=request.user_description,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await enqueue_job(
        FunctionNames.REBUILD_ORG_KNOWLEDGE_INDEX,
        user.selected_organization_id,
    )

    return _to_document_response(document)


@router.post(
    "/documents/{document_uuid}/re-extract",
    status_code=202,
    summary="Re-extract the DocCard for a document",
)
async def re_extract_doc_card(
    document_uuid: str,
    user=Depends(get_user),
):
    """Force re-extraction of the DocCard.

    Blocked at 400 if user_description is still null (e.g. legacy uploads).

    Access Control:
    * Users can only re-extract documents from their organization.
    """
    document = await db_client.get_document_by_uuid(
        document_uuid=document_uuid,
        organization_id=user.selected_organization_id,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if not document.user_description:
        raise HTTPException(
            status_code=400,
            detail="Cannot re-extract: document has no user_description. PATCH the document first.",
        )

    await enqueue_document_processing(
        document_id=document.id,
        s3_key=(document.custom_metadata or {}).get("s3_key", ""),
        organization_id=user.selected_organization_id,
        created_by_provider_id=str(user.provider_id),
        retrieval_mode=document.retrieval_mode,
    )
    return {"status": "enqueued", "document_uuid": document_uuid}


@router.post(
    "/search",
    response_model=ChunkSearchResponseSchema,
    summary="Search for similar chunks",
)
async def search_chunks(
    request: ChunkSearchRequestSchema,
    user=Depends(get_user),
):
    """Search for document chunks similar to the query.

    This endpoint uses vector similarity search to find relevant chunks.
    Results are returned without threshold filtering - apply similarity
    thresholds at the application layer after optional reranking.

    Access Control:
    * Users can only search documents from their organization.
    """

    try:
        # Import here to avoid circular dependency
        from api.services.configuration.ai_model_configuration import (
            apply_managed_embeddings_base_url,
            get_resolved_ai_model_configuration,
        )
        from api.services.gen_ai import build_embedding_service

        # Try to get the organization's embeddings configuration
        resolved_config = await get_resolved_ai_model_configuration(
            organization_id=user.selected_organization_id,
        )
        effective_config = resolved_config.effective
        embeddings_api_key = None
        embeddings_model = None
        embeddings_provider = None
        embeddings_base_url = None
        embeddings_endpoint = None
        embeddings_api_version = None

        if effective_config.embeddings:
            embeddings_api_key = effective_config.embeddings.api_key
            embeddings_model = effective_config.embeddings.model
            embeddings_provider = getattr(effective_config.embeddings, "provider", None)
            embeddings_endpoint = getattr(effective_config.embeddings, "endpoint", None)
            embeddings_base_url = apply_managed_embeddings_base_url(
                provider=embeddings_provider,
                base_url=getattr(effective_config.embeddings, "base_url", None),
            )
            embeddings_api_version = getattr(
                effective_config.embeddings, "api_version", None
            )

        # Manual search runs outside any workflow run, so resolve the MPS
        # correlation id here.
        embedding_service = await build_embedding_service(
            db_client=db_client,
            provider=embeddings_provider,
            api_key=embeddings_api_key,
            model=embeddings_model,
            base_url=embeddings_base_url,
            endpoint=embeddings_endpoint,
            api_version=embeddings_api_version,
            resolve_correlation=True,
        )

        # Perform search
        results = await embedding_service.search_similar_chunks(
            query=request.query,
            organization_id=user.selected_organization_id,
            limit=request.limit,
            document_uuids=request.document_uuids,
        )

        # Apply similarity threshold if provided
        if request.min_similarity is not None:
            results = [r for r in results if r["similarity"] >= request.min_similarity]

        # Convert to response schema
        from api.schemas.knowledge_base import ChunkResponseSchema

        chunks = [
            ChunkResponseSchema(
                id=r["id"],
                document_id=r["document_id"],
                chunk_text=r["chunk_text"],
                contextualized_text=r.get("contextualized_text"),
                chunk_index=r["chunk_index"],
                chunk_metadata=r["chunk_metadata"],
                filename=r["filename"],
                document_uuid=r["document_uuid"],
                similarity=r["similarity"],
            )
            for r in results
        ]

        return ChunkSearchResponseSchema(
            chunks=chunks,
            query=request.query,
            total_results=len(chunks),
        )

    except Exception as exc:
        logger.error(f"Error searching chunks: {exc}")
        raise HTTPException(status_code=500, detail="Failed to search chunks") from exc


DESCRIBE_PREVIEW_MAX_BYTES = 5 * 1024 * 1024
DESCRIBE_PREVIEW_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".json", ".md"}


@router.post(
    "/describe-preview",
    response_model=DescribePreviewResponseSchema,
    summary="Auto-draft a description for an uploaded document",
)
async def describe_preview(
    file: UploadFile = File(...),
    doc_type: Optional[str] = Form(default=None),
    intended_use: Optional[List[str]] = Form(default=None),
    user=Depends(get_user),
):
    """Generate a draft 'Describe this document' string via the LLM.

    The file is parsed once via MPS; the parse is cached for 30 minutes
    keyed by sha256 so the eventual Upload & Process reuses it. No DB
    record is created here.

    Access Control:
    * Authenticated users only. No org scoping needed — nothing is persisted.
    """
    filename = file.filename or "upload"
    extension = os.path.splitext(filename)[1].lower()
    if extension not in DESCRIBE_PREVIEW_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Allowed: "
                + ", ".join(sorted(DESCRIBE_PREVIEW_ALLOWED_EXTENSIONS))
            ),
        )

    content = await file.read()
    if len(content) > DESCRIBE_PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 5 MB limit")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
    try:
        tmp.write(content)
        tmp.close()

        try:
            result = await generate_description_preview(
                file_path=tmp.name,
                filename=filename,
                mime_type=file.content_type or "application/octet-stream",
                doc_type=doc_type,
                intended_use=intended_use,
                user_id=user.id,
                organization_id=user.selected_organization_id,
                created_by_provider_id=str(user.provider_id),
            )
        except DescribePreviewError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc

        return DescribePreviewResponseSchema(
            description=result.description,
            from_cache=result.from_cache,
        )
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
