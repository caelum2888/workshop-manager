"""Importação de turmas e alunos por planilha. Só admin; nunca grava sem confirmação."""

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin, require_admin_page
from app.models import User
from app.routers.pages import render
from app.services import imports
from app.services.errors import InvalidData

router = APIRouter(tags=["importação"])

TEMPLATE_MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get("/importar", include_in_schema=False)
def import_page(request: Request, current_user: User = Depends(require_admin_page)):
    return render(request, "import_students.html", "importar", current_user)


@router.get("/api/imports/students/template.{fmt}", summary="Baixar modelo da planilha de importação")
def download_template(fmt: str, _admin: User = Depends(require_admin)):
    if fmt not in TEMPLATE_MEDIA_TYPES:
        raise InvalidData("Formato de modelo inválido.")
    content = imports.build_template(fmt)
    return Response(
        content, media_type=TEMPLATE_MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="modelo-importacao.{fmt}"'},
    )


async def _read_upload(file: UploadFile) -> bytes:
    """Lê em pedaços e rejeita cedo se passar do limite, em vez de bufferizar um upload gigante inteiro."""
    if not file.filename:
        raise InvalidData("Envie um arquivo .csv ou .xlsx.")
    chunks, total = [], 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > imports.MAX_FILE_BYTES:
            raise InvalidData(f"Arquivo muito grande (máximo {imports.MAX_FILE_BYTES // (1024 * 1024)} MB).")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/imports/students/preview", summary="Prévia da importação (dry-run; não grava nada)")
async def preview_import(
    file: UploadFile = File(...), admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    raw = await _read_upload(file)
    result = imports.run_import(db, file.filename, raw, admin.organization_id, dry_run=True)
    return result.to_dict()


@router.post("/api/imports/students/confirm", summary="Confirma e executa a importação")
async def confirm_import(
    file: UploadFile = File(...), admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    # Nunca confia na prévia mostrada ao usuário: reprocessa o arquivo do zero,
    # com o tenant sempre vindo da sessão do admin autenticado.
    raw = await _read_upload(file)
    result = imports.run_import(db, file.filename, raw, admin.organization_id, dry_run=False)
    if not result.imported:
        raise InvalidData(
            "A planilha tem erros ou conflitos pendentes; corrija e reenvie antes de confirmar.",
            result.to_dict(),
        )
    return result.to_dict()
