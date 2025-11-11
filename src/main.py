import tempfile
from pathlib import Path
import asyncio

from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from starlette.middleware.cors import CORSMiddleware

from src.services.full_statement import parse_full_statement
from src.utils import logger


app = FastAPI(title="VTB Statement Parser API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/parse_statement", response_class=JSONResponse)
async def parse_report(file: UploadFile = File(...)):
    filename = Path(file.filename).name if file.filename else "uploaded.xlsx"
    logger.info("Получен файл: %s (content_type=%s)", filename, file.content_type)

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
            tmp_path = Path(tmp.name)
            content = await file.read()
            tmp.write(content)
            tmp.flush()
    except Exception as e:
        logger.exception("Ошибка сохранения загруженного файла: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось сохранить файл")

    try:
        result = await asyncio.to_thread(parse_full_statement, str(tmp_path))
    except Exception as e:
        logger.exception("Ошибка парсинга: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ошибка парсинга: {e}")
    finally:
        # гарантируем удаление temp-файла
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            logger.debug("Не удалось удалить временный файл %s", tmp_path)
    tmp_path.unlink(missing_ok=True)

    ops_count = len(result.get("operations", []))
    fin_ops = result.get("meta", {}).get("fin_ops_raw_count", 0)
    trade_ops = result.get("meta", {}).get("trade_ops_raw_count", 0)
    unknown_fin_ops = result.get("meta", {}).get("unknown_fin_ops", [])

    logger.info(
        "%s Аккаунт: %s, операций: %s финансовых операций: %s, операции с ценными бумагами: %s, "
        "не распознанные финансовые операции: %s%s",
        filename,
        result.get("account_id"),
        ops_count,
        fin_ops,
        trade_ops,
        len(unknown_fin_ops),
        (": " + ", ".join(unknown_fin_ops) if unknown_fin_ops else ""),
    )

    return JSONResponse(content=jsonable_encoder(result))

